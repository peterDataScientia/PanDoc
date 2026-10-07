from __future__ import annotations

import io
import json
import secrets
import uuid
import zipfile
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from js import Uint8Array
from pydantic import BaseModel, Field
from pyodide.ffi import to_js
from workers import asgi, fetch

app = FastAPI(
    title="PanDoc API",
    version="0.3.0",
    description="Cloudflare Workers gateway with private R2 inputs and GitHub Actions compute.",
)
Default = asgi.entrypoint(app)


def env_value(request: Request, name: str, default: str = "") -> str:
    value = getattr(request.scope["env"], name, default)
    return str(value) if value is not None else default


def repo(request: Request) -> str:
    return env_value(request, "PANDOC_GITHUB_REPOSITORY", "peterDataScientia/PanDoc")


def branch(request: Request) -> str:
    return env_value(request, "PANDOC_GITHUB_BRANCH", "main")


def bucket(request: Request):
    return request.scope["env"].PANDOC_JOBS


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    expected = env_value(request, "PANDOC_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="PANDOC_API_KEY is not configured.")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def require_internal(request: Request, authorization: str | None):
    expected = env_value(request, "PANDOC_WORKER_SECRET")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized.")


async def r2_put(request: Request, key: str, data: bytes):
    await bucket(request).put(key, to_js(data))


async def r2_get(request: Request, key: str) -> bytes | None:
    obj = await bucket(request).get(key)
    if obj is None:
        return None
    return bytes(Uint8Array.new(await obj.arrayBuffer()).to_py())


async def r2_delete(request: Request, key: str):
    await bucket(request).delete(key)


async def gh(request: Request, method: str, path: str, payload: dict | None = None):
    token = env_value(request, "GITHUB_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="GitHub credential is not configured.")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    kwargs = {"method": method, "headers": headers}
    if payload is not None:
        kwargs["body"] = json.dumps(payload)
    response = await fetch(f"https://api.github.com/repos/{repo(request)}{path}", **kwargs)
    if int(response.status) >= 400:
        detail = str(await response.text())[:1000]
        raise HTTPException(status_code=502, detail=f"GitHub API {response.status}: {detail}")
    return response


async def dispatch(request: Request, job_id: str, operation: str):
    await gh(
        request,
        "POST",
        "/actions/workflows/pandoc-api-compute.yml/dispatches",
        {"ref": branch(request), "inputs": {"job_id": job_id, "operation": operation}},
    )


async def store_job(request: Request, job_id: str, operation: str, files: dict[str, bytes]):
    prefix = f"jobs/{job_id}/"
    for rel, data in files.items():
        await r2_put(request, prefix + rel, data)
    manifest = {"job_id": job_id, "operation": operation, "files": sorted(files)}
    await r2_put(request, prefix + "manifest.json", json.dumps(manifest).encode())


async def find_run(request: Request, job_id: str):
    query = urlencode({"branch": branch(request), "event": "workflow_dispatch", "per_page": 100})
    response = await gh(request, "GET", f"/actions/workflows/pandoc-api-compute.yml/runs?{query}")
    payload = json.loads(str(await response.text()))
    return next(
        (run for run in payload.get("workflow_runs", []) if job_id in str(run.get("display_title", ""))),
        None,
    )


def run_state(run: dict | None) -> dict:
    if run is None:
        return {"state": "queued", "completed": 0, "total": 1}
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status in ("queued", "requested", "waiting", "pending"):
        state = "queued"
    elif status == "in_progress":
        state = "running"
    elif status == "completed" and conclusion == "success":
        state = "completed"
    elif status == "completed" and conclusion == "cancelled":
        state = "cancelled"
    elif status == "completed":
        state = "failed"
    else:
        state = status or "starting"
    return {
        "state": state,
        "completed": 1 if state == "completed" else 0,
        "total": 1,
        "conclusion": conclusion,
        "run_id": run.get("id"),
    }


async def artifact_bytes(request: Request, job_id: str) -> bytes:
    run = await find_run(request, job_id)
    state = run_state(run)
    if state["state"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {state['state']}; artifact is not ready.")
    response = await gh(request, "GET", f"/actions/runs/{run['id']}/artifacts")
    payload = json.loads(str(await response.text()))
    expected = f"pandoc-job-{job_id}"
    item = next(
        (x for x in payload.get("artifacts", []) if x.get("name") == expected and not x.get("expired")),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Result artifact is unavailable.")
    response = await gh(request, "GET", f"/actions/artifacts/{item['id']}/zip")
    return bytes(Uint8Array.new(await response.arrayBuffer()).to_py())


def queued(job_id: str, operation: str) -> dict:
    return {
        "job_id": job_id,
        "operation": operation,
        "state": "queued",
        "status_url": f"/api/v1/jobs/{job_id}",
        "results_url": f"/api/v1/jobs/{job_id}/results",
        "bundle_url": f"/api/v1/jobs/{job_id}/bundle",
    }


class MicrostatesRequest(BaseModel):
    smiles: str = Field(min_length=1)
    ph: float = Field(default=7.0, ge=0.0, le=14.0)
    max_states: int = Field(default=16, ge=1, le=32)


class CandidateItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    smiles: str = Field(min_length=1)


class CandidatePrepareRequest(BaseModel):
    ligands: list[CandidateItem] = Field(min_length=1, max_length=25)
    ph: float = Field(default=7.0, ge=0.0, le=14.0)
    enumerate_states: bool = True


@app.get("/")
async def root():
    return {"name": "PanDoc API", "version": app.version, "docs": "/docs", "health": "/api/v1/health"}


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "gateway": "cloudflare-workers", "compute_backend": "github-actions"}


@app.get("/api/v1/compute", dependencies=[Depends(require_api_key)])
async def compute_info(request: Request):
    return {
        "gateway": "cloudflare-workers",
        "input_storage": "private-r2",
        "compute_backend": "github-actions",
        "repository": repo(request),
    }


@app.post("/api/v1/jobs/microstates", dependencies=[Depends(require_api_key)])
async def submit_microstates(request: Request, body: MicrostatesRequest):
    job_id = uuid.uuid4().hex
    payload = {"smiles": body.smiles, "ph": body.ph, "max_states": body.max_states}
    await store_job(request, job_id, "ligand-microstates", {"request.json": json.dumps(payload).encode()})
    await dispatch(request, job_id, "ligand-microstates")
    return queued(job_id, "ligand-microstates")


@app.post("/api/v1/jobs/candidates", dependencies=[Depends(require_api_key)])
async def submit_candidates(request: Request, body: CandidatePrepareRequest):
    job_id = uuid.uuid4().hex
    payload = {
        "ligands": [item.model_dump() for item in body.ligands],
        "ph": body.ph,
        "enumerate_states": body.enumerate_states,
    }
    await store_job(request, job_id, "prepare-candidates", {"request.json": json.dumps(payload).encode()})
    await dispatch(request, job_id, "prepare-candidates")
    return queued(job_id, "prepare-candidates")


@app.post("/api/v1/jobs/dock", dependencies=[Depends(require_api_key)])
async def submit_dock(
    request: Request,
    config: str = Form(...),
    receptor: UploadFile = File(...),
    ligands: list[UploadFile] = File(...),
    reference: UploadFile | None = File(None),
):
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="config must be valid JSON.") from exc
    if not 1 <= len(ligands) <= 25:
        raise HTTPException(status_code=422, detail="Provide between one and 25 ligand PDBQT files.")

    files: dict[str, bytes] = {}
    receptor_data = await receptor.read()
    if not receptor_data:
        raise HTTPException(status_code=422, detail="Receptor file is empty.")
    files["inputs/receptor.pdbqt"] = receptor_data
    cfg["receptor"] = "inputs/receptor.pdbqt"

    supplied = list(cfg.get("ligands") or [])
    portable = []
    for index, upload in enumerate(ligands, 1):
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=422, detail=f"Ligand {index} is empty.")
        rel = f"inputs/ligand_{index:03d}.pdbqt"
        files[rel] = data
        meta = dict(supplied[index - 1]) if index <= len(supplied) else {}
        meta["id"] = str(meta.get("id") or f"ligand_{index:03d}")
        meta["name"] = str(meta.get("name") or upload.filename or meta["id"])
        meta["path"] = rel
        portable.append(meta)
    cfg["ligands"] = portable

    if reference is not None:
        data = await reference.read()
        if data:
            files["inputs/reference.sdf"] = data
            cfg["reference"] = "inputs/reference.sdf"

    files["config.json"] = json.dumps(cfg, indent=2).encode()
    job_id = uuid.uuid4().hex
    await store_job(request, job_id, "dock", files)
    await dispatch(request, job_id, "dock")
    return queued(job_id, "dock")


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_api_key)])
async def job_status(request: Request, job_id: str):
    return {"job_id": job_id, **run_state(await find_run(request, job_id))}


@app.get("/api/v1/jobs/{job_id}/results", dependencies=[Depends(require_api_key)])
async def job_results(request: Request, job_id: str):
    data = await artifact_bytes(request, job_id)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            raw = archive.read("results.json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="results.json is missing.") from exc
    return json.loads(raw)


@app.get("/api/v1/jobs/{job_id}/bundle", dependencies=[Depends(require_api_key)])
async def job_bundle(request: Request, job_id: str):
    data = await artifact_bytes(request, job_id)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pandoc-{job_id}.zip"'},
    )


@app.get("/internal/jobs/{job_id}/{relpath:path}")
async def internal_get(
    request: Request,
    job_id: str,
    relpath: str,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    require_internal(request, authorization)
    if ".." in relpath.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path.")
    data = await r2_get(request, f"jobs/{job_id}/{relpath}")
    if data is None:
        raise HTTPException(status_code=404, detail="Object not found.")
    return Response(data, media_type="application/octet-stream")


@app.delete("/internal/jobs/{job_id}")
async def internal_delete(
    request: Request,
    job_id: str,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    require_internal(request, authorization)
    manifest_data = await r2_get(request, f"jobs/{job_id}/manifest.json")
    if manifest_data is None:
        return {"deleted": 0}
    manifest = json.loads(manifest_data)
    files = list(manifest.get("files", []))
    for rel in files:
        await r2_delete(request, f"jobs/{job_id}/{rel}")
    await r2_delete(request, f"jobs/{job_id}/manifest.json")
    return {"deleted": len(files) + 1}
