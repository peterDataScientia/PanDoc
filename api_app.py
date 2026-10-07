from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from pandoc import profile_engine


app = FastAPI(
    title="PanDoc Receptor Preparation API",
    version="1.0.0",
    description="Reviewed-profile receptor preparation API for PanDoc.",
)

JOBS = {}


@app.get("/api/v1/profiles")
def profiles():
    return profile_engine.available_profiles()


@app.post("/api/v1/receptors/prepare")
def prepare_receptor(payload: dict):
    target = str(payload.get("target", "")).strip()
    if not target:
        raise HTTPException(status_code=400, detail="target is required")

    job_id = uuid.uuid4().hex
    root = Path(tempfile.gettempdir()) / "pandoc_api_jobs" / job_id
    root.mkdir(parents=True, exist_ok=True)

    try:
        result = profile_engine.prepare_from_profile(
            target,
            root / target,
            force_curated=False,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        JOBS[job_id] = {"status": "failed", "error": str(exc), "root": str(root)}
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    bundle = root / "result.zip"
    bundle.write_bytes(profile_engine.core.bundle(root / target))
    JOBS[job_id] = {
        "status": "complete",
        "result": result,
        "bundle": str(bundle),
        "root": str(root),
    }
    return {"job_id": job_id, "status": "complete", "result": result}


@app.get("/api/v1/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    item = JOBS[job_id]
    return {k: v for k, v in item.items() if k not in {"bundle", "root"}}


@app.get("/api/v1/jobs/{job_id}/results")
def job_results(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    item = JOBS[job_id]
    if item.get("status") != "complete":
        raise HTTPException(status_code=409, detail="job is not complete")
    return FileResponse(
        item["bundle"],
        media_type="application/zip",
        filename=f"PanDoc_{job_id}_result.zip",
    )
