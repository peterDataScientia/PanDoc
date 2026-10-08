from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from pandoc.core import validate_config

ROOT = Path(os.environ.get("PANDOC_KAGGLE_DATA_DIR", "/kaggle/working/pandoc_direct")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
API_KEY = os.environ.get("PANDOC_KAGGLE_API_KEY", "").strip()

app = FastAPI(title="PanDoc Kaggle Direct Backend", version="0.1.0")


def available_cpu_count() -> int:
    """Return CPUs actually available to this Kaggle process/cgroup."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, int(os.cpu_count() or 1))


def auth(x_api_key: str | None) -> None:
    if not API_KEY:
        raise HTTPException(503, "PANDOC_KAGGLE_API_KEY is not configured on the Kaggle worker.")
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key.")


def job_dir(job_id: str) -> Path:
    if not job_id or any(c not in "0123456789abcdef" for c in job_id.lower()):
        raise HTTPException(404, "Unknown job.")
    path = (ROOT / job_id).resolve()
    if ROOT not in path.parents:
        raise HTTPException(404, "Unknown job.")
    return path


def safe_extract(data: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if target.resolve() not in destination.parents and destination != target.resolve():
                raise HTTPException(400, "Unsafe path in job archive.")
        archive.extractall(target)


def load_portable(directory: Path) -> dict:
    try:
        return json.loads((directory / "config.portable.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Job configuration is unreadable: {exc}")


def runtime_config(directory: Path, portable: dict) -> dict:
    runtime = json.loads(json.dumps(portable))
    runtime["receptor"] = str((directory / portable["receptor"]).resolve())
    runtime["ligands"] = [
        {**item, "path": str((directory / item["path"]).resolve())}
        for item in portable.get("ligands", [])
    ]
    if portable.get("reference"):
        runtime["reference"] = str((directory / portable["reference"]).resolve())

    # Computer B is a warm dedicated compute node. Ignore the conservative
    # frontend CPU cap and let AutoDock Vina use every CPU actually assigned
    # to this Kaggle session.
    runtime["cpu"] = available_cpu_count()

    validate_config(runtime)
    return runtime


def status_payload(directory: Path) -> dict:
    try:
        return json.loads((directory / "status.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"state": "starting", "completed": 0}


def make_portable(directory: Path) -> None:
    marker = directory / ".portable"
    if marker.exists():
        return
    portable = load_portable(directory)
    (directory / "config.json").write_text(json.dumps(portable, indent=2))
    marker.touch()


def launch_task(operation: str, request_payload: dict) -> dict:
    job_id = uuid.uuid4().hex
    directory = ROOT / job_id
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps(request_payload, indent=2))
    (directory / "status.json").write_text(json.dumps({"state": "queued", "completed": 0, "total": 1}))
    log = (directory / "worker.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "pandoc.kaggle_task_worker", str(directory), operation],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    (directory / "worker.pid").write_text(str(process.pid))
    return {"job_id": job_id, "operation": operation}


@app.get("/api/v1/health")
def health(x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    return {
        "ok": True,
        "backend": "kaggle-direct",
        "cpu_count": available_cpu_count(),
        "root": str(ROOT),
    }


@app.post("/api/v1/jobs/ligand-microstates")
async def submit_ligand_microstates(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    payload = await request.json()
    if not str(payload.get("smiles", "")).strip():
        raise HTTPException(400, "SMILES is required.")
    return launch_task("ligand-microstates", payload)


@app.post("/api/v1/jobs/prepare-candidates")
async def submit_candidate_preparation(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    payload = await request.json()
    if not payload.get("ligands"):
        raise HTTPException(400, "Candidate ligands are required.")
    return launch_task("prepare-candidates", payload)


@app.post("/api/v1/jobs/dock")
async def submit(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty job archive.")
    job_id = uuid.uuid4().hex
    directory = ROOT / job_id
    directory.mkdir(parents=True)
    try:
        safe_extract(data, directory)
        portable = json.loads((directory / "config.json").read_text())
        (directory / "config.portable.json").write_text(json.dumps(portable, indent=2))
        runtime = runtime_config(directory, portable)
        (directory / "config.json").write_text(json.dumps(runtime, indent=2))
        (directory / "status.json").write_text(json.dumps({"state": "queued", "completed": 0}))
        log = (directory / "worker.log").open("w")
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "pandoc.worker", str(directory)],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        (directory / "worker.pid").write_text(str(process.pid))
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return {"job_id": job_id}


@app.get("/api/v1/jobs/{job_id}")
def status(job_id: str, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    directory = job_dir(job_id)
    if not directory.exists():
        raise HTTPException(404, "Unknown job.")
    payload = status_payload(directory)
    if payload.get("state") == "completed" and (directory / "config.portable.json").exists():
        make_portable(directory)
    return payload


@app.get("/api/v1/jobs/{job_id}/logs", response_class=PlainTextResponse)
def logs(job_id: str, tail: int = 80, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    directory = job_dir(job_id)
    path = directory / "worker.log"
    if not path.exists():
        return "Waiting for worker."
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-max(1, min(int(tail), 500)):]) or "Waiting for worker."


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel(job_id: str, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    directory = job_dir(job_id)
    if not directory.exists():
        raise HTTPException(404, "Unknown job.")
    (directory / "cancel.request").touch()
    return {"ok": True}


@app.get("/api/v1/jobs/{job_id}/bundle")
def bundle(job_id: str, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    directory = job_dir(job_id)
    if not directory.exists():
        raise HTTPException(404, "Unknown job.")
    state = status_payload(directory)
    if state.get("state") != "completed":
        raise HTTPException(409, f"Job is {state.get('state', 'not complete')}.")
    if (directory / "config.portable.json").exists():
        make_portable(directory)
    bundle_path = ROOT / f"{job_id}.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in directory.rglob("*"):
            if path.is_file() and path.name not in {"worker.pid", "config.portable.json", ".portable"}:
                archive.write(path, path.relative_to(directory))
    return FileResponse(bundle_path, media_type="application/zip", filename=f"pandoc-{job_id}.zip")
