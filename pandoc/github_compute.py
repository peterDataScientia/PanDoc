from __future__ import annotations

import base64
import copy
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken


DEFAULT_REPOSITORY = "peterDataScientia/PanDoc"
WORKFLOW = "pandoc-compute.yml"


class GitHubComputeError(RuntimeError):
    pass


def validate_key(key: str) -> str:
    value = (key or "").strip()
    try:
        Fernet(value.encode())
    except Exception as exc:
        raise GitHubComputeError(
            "PANDOC_JOB_KEY must be a valid Fernet key. Generate one with "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"."
        ) from exc
    return value


def _request(token: str, method: str, url: str, **kwargs):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    response = requests.request(method, url, headers=headers, timeout=(15, 90), **kwargs)
    if response.status_code >= 400:
        detail = response.text[:1200]
        raise GitHubComputeError(f"GitHub API {response.status_code}: {detail}")
    return response


def _repo_url(repository: str, suffix: str) -> str:
    return f"https://api.github.com/repos/{repository}{suffix}"


def _encrypt_archive(files: dict[str, bytes], key: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return Fernet(validate_key(key).encode()).encrypt(stream.getvalue())


def _payload(config: dict, key: str) -> bytes:
    portable = copy.deepcopy(config)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        receptor = Path(config["receptor"])
        archive.write(receptor, "inputs/receptor.pdbqt")
        portable["receptor"] = "inputs/receptor.pdbqt"

        ligands = []
        for index, ligand in enumerate(config.get("ligands", []), 1):
            source = Path(ligand["path"])
            suffix = source.suffix or ".pdbqt"
            rel = f"inputs/ligand_{index:03d}{suffix}"
            archive.write(source, rel)
            item = dict(ligand)
            item["path"] = rel
            ligands.append(item)
        portable["ligands"] = ligands

        reference = config.get("reference")
        if reference:
            source = Path(reference)
            rel = "inputs/reference.sdf"
            archive.write(source, rel)
            portable["reference"] = rel

        archive.writestr("config.json", json.dumps(portable, indent=2))
    return Fernet(validate_key(key).encode()).encrypt(stream.getvalue())


def decrypt_payload(payload: bytes, key: str) -> bytes:
    try:
        return Fernet(validate_key(key).encode()).decrypt(payload)
    except InvalidToken as exc:
        raise GitHubComputeError("The encrypted PanDoc job could not be decrypted. Check PANDOC_JOB_KEY.") from exc


class GitHubCompute:
    def __init__(self, token: str, job_key: str, repository: str = DEFAULT_REPOSITORY):
        self.token = (token or "").strip()
        if not self.token:
            raise GitHubComputeError("GITHUB_TOKEN is missing.")
        self.job_key = validate_key(job_key)
        self.repository = repository

    def _queue(self, payload: bytes, operation: str) -> dict:
        job_id = uuid.uuid4().hex
        branch = f"pandoc-job-{job_id}"

        repo = _request(self.token, "GET", _repo_url(self.repository, "")).json()
        default_branch = repo.get("default_branch", "main")
        ref = _request(
            self.token,
            "GET",
            _repo_url(self.repository, f"/git/ref/heads/{quote(default_branch, safe='')}"),
        ).json()
        sha = ref["object"]["sha"]

        _request(
            self.token,
            "POST",
            _repo_url(self.repository, "/git/refs"),
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

        encoded = base64.b64encode(payload).decode()
        try:
            _request(
                self.token,
                "PUT",
                _repo_url(self.repository, f"/contents/.pandoc_jobs/{job_id}.bin"),
                json={
                    "message": f"Queue encrypted PanDoc {operation} job {job_id[:8]}",
                    "content": encoded,
                    "branch": branch,
                },
            )
            _request(
                self.token,
                "POST",
                _repo_url(self.repository, f"/actions/workflows/{WORKFLOW}/dispatches"),
                json={"ref": branch, "inputs": {"job_id": job_id, "operation": operation}},
            )
        except Exception:
            try:
                self.delete_branch(branch)
            except Exception:
                pass
            raise

        return {
            "job_id": job_id,
            "branch": branch,
            "repository": self.repository,
            "operation": operation,
            "submitted_at": time.time(),
        }

    def submit(self, config: dict) -> dict:
        return self._queue(_payload(config, self.job_key), "dock")

    def submit_ligand_microstates(self, smiles: str, ph: float, max_states: int = 16) -> dict:
        request = {
            "smiles": str(smiles),
            "ph": float(ph),
            "max_states": int(max_states),
        }
        payload = _encrypt_archive(
            {"request.json": json.dumps(request, indent=2).encode()},
            self.job_key,
        )
        return self._queue(payload, "ligand-microstates")

    def submit_candidate_preparation(self, ligands: list[dict], ph: float, enumerate_states: bool = True) -> dict:
        request = {
            "ligands": ligands,
            "ph": float(ph),
            "enumerate_states": bool(enumerate_states),
        }
        payload = _encrypt_archive(
            {"request.json": json.dumps(request, indent=2).encode()},
            self.job_key,
        )
        return self._queue(payload, "prepare-candidates")

    def _run(self, job: dict):
        params = {
            "branch": job["branch"],
            "event": "workflow_dispatch",
            "per_page": 20,
        }
        runs = _request(
            self.token,
            "GET",
            _repo_url(self.repository, f"/actions/workflows/{WORKFLOW}/runs"),
            params=params,
        ).json().get("workflow_runs", [])
        matching = [r for r in runs if job["job_id"] in (r.get("display_title") or "")]
        if matching:
            return matching[0]

        recent = _request(
            self.token,
            "GET",
            _repo_url(self.repository, f"/actions/workflows/{WORKFLOW}/runs"),
            params={"event": "workflow_dispatch", "per_page": 100},
        ).json().get("workflow_runs", [])
        matching = [r for r in recent if job["job_id"] in (r.get("display_title") or "")]
        return matching[0] if matching else None

    def status(self, job: dict) -> dict:
        run = self._run(job)
        if run is None:
            return {
                "state": "queued",
                "completed": 0,
                "message": "Waiting for GitHub Actions to register the job.",
            }
        gh_status = run.get("status")
        conclusion = run.get("conclusion")
        if gh_status in ("queued", "requested", "waiting", "pending"):
            state = "queued"
        elif gh_status in ("in_progress",):
            state = "running"
        elif gh_status == "completed" and conclusion == "success":
            state = "completed"
        elif gh_status == "completed" and conclusion == "cancelled":
            state = "cancelled"
        elif gh_status == "completed":
            state = "failed"
        else:
            state = gh_status or "starting"
        result = {
            "state": state,
            "run_id": run["id"],
            "html_url": run.get("html_url"),
            "conclusion": conclusion,
            "completed": 1 if state == "completed" else 0,
            "total": 1,
        }
        if state == "failed":
            try:
                jobs_data = _request(
                    self.token,
                    "GET",
                    _repo_url(self.repository, f"/actions/runs/{run['id']}/jobs"),
                ).json().get("jobs", [])
                failed_job = next((j for j in jobs_data if j.get("conclusion") == "failure"), None)
                if failed_job:
                    log = _request(
                        self.token,
                        "GET",
                        _repo_url(self.repository, f"/actions/jobs/{failed_job['id']}/logs"),
                    ).text
                    if "PANDOC_JOB_KEY repository secret is not configured." in log:
                        result["error"] = "GitHub Actions is missing the PANDOC_JOB_KEY repository secret."
                    else:
                        lines = [line.strip() for line in log.splitlines() if line.strip()]
                        result["error"] = lines[-1][-500:] if lines else "GitHub Actions compute failed."
            except Exception:
                result["error"] = "GitHub Actions compute failed."
        return result

    def logs(self, job: dict, tail: int = 80) -> str:
        run = self._run(job)
        if run is None:
            return "Waiting for GitHub Actions to register the job."
        try:
            jobs_data = _request(
                self.token,
                "GET",
                _repo_url(self.repository, f"/actions/runs/{run['id']}/jobs"),
            ).json().get("jobs", [])
            if not jobs_data:
                return "Waiting for the compute job to start."
            active = next(
                (j for j in jobs_data if j.get("status") == "in_progress"),
                jobs_data[-1],
            )
            response = _request(
                self.token,
                "GET",
                _repo_url(self.repository, f"/actions/jobs/{active['id']}/logs"),
            )
            lines = []
            for line in response.text.splitlines():
                line = re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s*", "", line)
                line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
                if line.strip():
                    lines.append(line.rstrip())
            return "\n".join(lines[-max(1, int(tail)):]) or "Waiting for compute output."
        except Exception:
            return "Live log is not available yet."

    def cancel(self, job: dict):
        run = self._run(job)
        if run is None:
            return False
        response = _request(
            self.token,
            "POST",
            _repo_url(self.repository, f"/actions/runs/{run['id']}/cancel"),
        )
        return response.status_code in (202, 204)

    def artifact(self, job: dict) -> bytes:
        run = self._run(job)
        if run is None or run.get("status") != "completed":
            raise GitHubComputeError("The GitHub Actions run is not complete.")
        artifacts = _request(
            self.token,
            "GET",
            _repo_url(self.repository, f"/actions/runs/{run['id']}/artifacts"),
        ).json().get("artifacts", [])
        expected = f"pandoc-job-{job['job_id']}"
        artifact = next((a for a in artifacts if a.get("name") == expected and not a.get("expired")), None)
        if artifact is None:
            raise GitHubComputeError("The PanDoc result artifact is not available.")
        response = _request(
            self.token,
            "GET",
            _repo_url(self.repository, f"/actions/artifacts/{artifact['id']}/zip"),
        )
        return response.content

    def materialize(self, job: dict, target: str | Path) -> Path:
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        payload = self.artifact(job)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.infolist():
                destination = (target / member.filename).resolve()
                if target.resolve() not in destination.parents and destination != target.resolve():
                    raise GitHubComputeError("Unsafe path in GitHub Actions artifact.")
            archive.extractall(target)
        return target

    def delete_branch(self, branch: str):
        _request(
            self.token,
            "DELETE",
            _repo_url(self.repository, f"/git/refs/heads/{quote(branch, safe='')}"),
        )

    def cleanup(self, job: dict):
        try:
            self.delete_branch(job["branch"])
        except GitHubComputeError as exc:
            if "422" not in str(exc) and "404" not in str(exc):
                raise
