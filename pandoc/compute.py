from __future__ import annotations

import os
from pathlib import Path

from . import jobs
from .github_compute import DEFAULT_REPOSITORY, GitHubCompute, GitHubComputeError


class ComputeBackendError(RuntimeError):
    pass


class LocalCompute:
    """Local subprocess backend used for development/fallback."""

    name = "local"

    def submit(self, root: str | Path, config: dict) -> dict:
        directory = Path(jobs.launch(root, config))
        return {
            "backend": self.name,
            "job_id": directory.name,
            "directory": str(directory),
        }

    def status(self, handle: dict) -> dict:
        return jobs.status(Path(handle["directory"]))

    def cancel(self, handle: dict) -> bool:
        jobs.cancel(Path(handle["directory"]))
        return True

    def materialize(self, handle: dict, target: str | Path | None = None) -> Path:
        return Path(handle["directory"])

    def cleanup(self, handle: dict) -> None:
        return None


class GitHubActionsCompute:
    """Encrypted GitHub Actions compute backend shared by the UI and API."""

    name = "github-actions"

    def __init__(self, token: str, job_key: str, repository: str = DEFAULT_REPOSITORY):
        try:
            self.client = GitHubCompute(token, job_key, repository)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc

    @property
    def repository(self) -> str:
        return self.client.repository

    def submit(self, root: str | Path, config: dict) -> dict:
        del root  # GitHub Actions does not need a local worker root.
        try:
            remote = self.client.submit(config)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc
        return {"backend": self.name, **remote}

    def status(self, handle: dict) -> dict:
        try:
            return self.client.status(handle)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc

    def cancel(self, handle: dict) -> bool:
        try:
            return self.client.cancel(handle)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc

    def materialize(self, handle: dict, target: str | Path | None = None) -> Path:
        if target is None:
            raise ComputeBackendError("GitHub Actions results require a local target directory.")
        try:
            return self.client.materialize(handle, target)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc

    def cleanup(self, handle: dict) -> None:
        try:
            self.client.cleanup(handle)
        except GitHubComputeError as exc:
            raise ComputeBackendError(str(exc)) from exc


def from_credentials(
    token: str = "",
    job_key: str = "",
    repository: str = DEFAULT_REPOSITORY,
    *,
    allow_local: bool = True,
):
    token = (token or "").strip()
    job_key = (job_key or "").strip()
    if token and job_key:
        return GitHubActionsCompute(token, job_key, repository)
    if token or job_key:
        raise ComputeBackendError("Both GITHUB_TOKEN and PANDOC_JOB_KEY are required for GitHub Actions compute.")
    if allow_local:
        return LocalCompute()
    raise ComputeBackendError("GitHub Actions compute is not configured.")


def from_environment(*, allow_local: bool = True):
    return from_credentials(
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("PANDOC_JOB_KEY", ""),
        os.environ.get("PANDOC_GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
        allow_local=allow_local,
    )
