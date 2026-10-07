from __future__ import annotations

import os
from pathlib import Path

from .github_compute import DEFAULT_REPOSITORY, GitHubCompute, GitHubComputeError


class ComputeBackendError(RuntimeError):
    pass


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
        del root
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
):
    token = (token or "").strip()
    job_key = (job_key or "").strip()
    if not token or not job_key:
        raise ComputeBackendError(
            "GitHub Actions compute is required. Configure both GITHUB_TOKEN and PANDOC_JOB_KEY."
        )
    return GitHubActionsCompute(token, job_key, repository)


def from_environment():
    return from_credentials(
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("PANDOC_JOB_KEY", ""),
        os.environ.get("PANDOC_GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
    )
