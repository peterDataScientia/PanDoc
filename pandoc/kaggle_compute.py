from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import requests


class KaggleComputeError(RuntimeError):
    pass


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if target not in destination.parents and destination != target:
            raise KaggleComputeError("Unsafe path in Kaggle result bundle.")
    archive.extractall(target)


class KaggleDirectCompute:
    """Client for a warm PanDoc FastAPI worker running inside a Kaggle session."""

    name = "kaggle-direct"

    def __init__(self, base_url: str, api_key: str, timeout: int = 90):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout)
        if not self.base_url.startswith(("https://", "http://")):
            raise KaggleComputeError("PANDOC_KAGGLE_URL must be an http(s) URL.")
        if not self.api_key:
            raise KaggleComputeError("PANDOC_KAGGLE_API_KEY is required.")

    @property
    def headers(self):
        return {"X-API-Key": self.api_key}

    def _request(self, method: str, path: str, **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))
        try:
            response = requests.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=kwargs.pop("timeout", (10, self.timeout)),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise KaggleComputeError(f"Kaggle direct backend is unreachable: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:800]
            raise KaggleComputeError(f"Kaggle direct backend {response.status_code}: {detail}")
        return response

    def health(self) -> dict:
        return self._request("GET", "/api/v1/health", timeout=(5, 15)).json()

    def submit(self, root: str | Path, config: dict) -> dict:
        del root
        stream = io.BytesIO()
        portable = json.loads(json.dumps(config))
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            receptor = Path(config["receptor"])
            archive.write(receptor, "inputs/receptor.pdbqt")
            portable["receptor"] = "inputs/receptor.pdbqt"
            prepared_pdb = receptor.parent / "receptor_prepared.pdb"
            if prepared_pdb.exists():
                archive.write(prepared_pdb, "inputs/receptor_prepared.pdb")

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

            if config.get("reference"):
                source = Path(config["reference"])
                archive.write(source, "inputs/reference.sdf")
                portable["reference"] = "inputs/reference.sdf"

            archive.writestr("config.json", json.dumps(portable, indent=2))

        response = self._request(
            "POST",
            "/api/v1/jobs/dock",
            data=stream.getvalue(),
            headers={"Content-Type": "application/zip"},
            timeout=(10, 120),
        ).json()
        return {"backend": self.name, **response}

    def status(self, handle: dict) -> dict:
        return self._request("GET", f"/api/v1/jobs/{handle['job_id']}").json()

    def progress(self, handle: dict) -> dict:
        state = self.status(handle)
        steps = [{"name": "Computer B ready", "status": "completed", "conclusion": "success"}]
        if state.get("state") in ("queued", "starting"):
            steps.append({"name": "Docking queued", "status": "queued", "conclusion": None})
        elif state.get("state") == "running":
            name = state.get("current") or "Docking"
            steps.append({"name": name, "status": "in_progress", "conclusion": None})
        elif state.get("state") == "completed":
            steps.append({"name": "Docking completed", "status": "completed", "conclusion": "success"})
        elif state.get("state") in ("failed", "cancelled"):
            steps.append({
                "name": "Docking " + state["state"],
                "status": "completed",
                "conclusion": "failure" if state["state"] == "failed" else "cancelled",
            })
        return {"state": state.get("state", "starting"), "steps": steps}

    def logs(self, handle: dict, tail: int = 80) -> str:
        response = self._request("GET", f"/api/v1/jobs/{handle['job_id']}/logs?tail={max(1, int(tail))}")
        return response.text

    def cancel(self, handle: dict) -> bool:
        payload = self._request("POST", f"/api/v1/jobs/{handle['job_id']}/cancel").json()
        return bool(payload.get("ok"))

    def materialize(self, handle: dict, target: str | Path | None = None) -> Path:
        if target is None:
            raise KaggleComputeError("Kaggle direct results require a local target directory.")
        target = Path(target)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        response = self._request(
            "GET",
            f"/api/v1/jobs/{handle['job_id']}/bundle",
            timeout=(10, 180),
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            _safe_extract(archive, target)
        return target

    def cleanup(self, handle: dict) -> None:
        # Keep completed results in /kaggle/working until the session ends. This makes
        # retries cheap and avoids deleting a bundle before the Streamlit download finishes.
        return None
