from __future__ import annotations

import json
import os
import re
import uuid
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pandoc import compute, core, jobs, phprep, task_agent, agent_planner, agent_conversation


API_ROOT = Path(os.environ.get("PANDOC_API_DATA_DIR", "/tmp/pandoc_api")).resolve()
API_ROOT.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = int(os.environ.get("PANDOC_API_MAX_UPLOAD_MB", "25")) * 1024 * 1024
API_KEY = os.environ.get("PANDOC_API_KEY", "").strip()

app = FastAPI(
    title="PanDoc API",
    version="0.2.0",
    description="Programmatic access to PanDoc preparation and encrypted GitHub Actions docking compute.",
    docs_url="/docs",
    redoc_url="/redoc",
)

cors = [x.strip() for x in os.environ.get("PANDOC_CORS_ORIGINS", "").split(",") if x.strip()]
if cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def require_api_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


async def read_upload(upload: UploadFile) -> bytes:
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured size limit.")
    return data


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="PanDoc expects text-based molecular files.") from exc


def safe_name(name: str | None, fallback: str) -> str:
    value = Path(name or fallback).name
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value or fallback


def domain_error(exc: Exception):
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    path = API_ROOT / "jobs" / job_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Job not found.")
    return path


def compute_backend(*, require_github: bool = True):
    del require_github
    try:
        return compute.from_environment()
    except compute.ComputeBackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def remote_record_path(directory: Path) -> Path:
    return directory / "remote_job.json"


def load_remote_job(directory: Path) -> dict | None:
    path = remote_record_path(directory)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Stored remote job metadata is unreadable.") from exc


def save_remote_job(directory: Path, handle: dict):
    remote_record_path(directory).write_text(json.dumps(handle, indent=2))


def sync_remote_job(directory: Path, *, materialize: bool = False) -> dict:
    handle = load_remote_job(directory)
    if handle is None:
        raise HTTPException(status_code=500, detail="GitHub Actions job metadata is missing.")

    if handle.get("materialized") and (directory / "status.json").is_file():
        return jobs.status(directory)

    backend = compute_backend(require_github=True)
    try:
        state = backend.status(handle)
    except compute.ComputeBackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    (directory / "status.json").write_text(json.dumps(state, indent=2))

    if state.get("state") == "completed" and materialize:
        try:
            backend.materialize(handle, directory)
            backend.cleanup(handle)
        except compute.ComputeBackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        handle["materialized"] = True
        save_remote_job(directory, handle)
        if (directory / "status.json").is_file():
            return jobs.status(directory)
    return state


class LigandMicrostatesRequest(BaseModel):
    smiles: str = Field(min_length=1)
    ph: float = Field(default=7.0, ge=0.0, le=14.0)
    max_states: int = Field(default=16, ge=1, le=32)


class LigandPrepareRequest(BaseModel):
    smiles: str = Field(min_length=1)
    name: str = Field(default="ligand", min_length=1, max_length=120)


@app.get("/")
def root():
    return {
        "name": "PanDoc API",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/api/v1/health")
def health():
    try:
        backend = compute.from_environment()
        backend_name = backend.name
    except compute.ComputeBackendError:
        backend_name = "unavailable"
    return {
        "status": "ok",
        "service": "pandoc-api",
        "version": app.version,
        "compute_backend": backend_name,
    }


@app.get("/api/v1/compute", dependencies=[Depends(require_api_key)])
def compute_status():
    backend = compute_backend()
    payload = {"backend": backend.name}
    if backend.name == "github-actions":
        payload["repository"] = backend.repository
        payload["encrypted_payloads"] = True
    return payload


@app.get("/api/v1/versions", dependencies=[Depends(require_api_key)])
def versions():
    return core.versions()


@app.post("/api/v1/structures/inspect", dependencies=[Depends(require_api_key)])
async def inspect_structure(file: UploadFile = File(...)):
    data = await read_upload(file)
    text = decode_text(data)
    suffix = Path(file.filename or "structure.pdb").suffix or ".pdb"
    try:
        pdb = core.normalize_structure(text, suffix)
        residues = core.inspect(pdb)
        atom_count = len(core.atoms(pdb))
    except ValueError as exc:
        domain_error(exc)
    return {
        "filename": safe_name(file.filename, "structure.pdb"),
        "atoms": atom_count,
        "residues": residues,
        "structure_sha256": core.digest(pdb),
    }


@app.post("/api/v1/proteins/protonation", dependencies=[Depends(require_api_key)])
async def protein_protonation(
    file: UploadFile = File(...),
    ph: float = Form(7.0),
    repair_missing_heavy_atoms: bool = Form(True),
):
    if not 0.0 <= ph <= 14.0:
        raise HTTPException(status_code=422, detail="pH must be between 0 and 14.")
    data = await read_upload(file)
    text = decode_text(data)
    suffix = Path(file.filename or "receptor.pdb").suffix or ".pdb"
    work = API_ROOT / "protonation" / uuid.uuid4().hex
    try:
        pdb = core.normalize_structure(text, suffix)
        if repair_missing_heavy_atoms:
            pdb = core.repair_heavy_atoms(pdb)
        rows, _ = phprep.predict_protein_states(pdb, work, ph)
    except (ValueError, RuntimeError) as exc:
        domain_error(exc)
    return {
        "pH": ph,
        "repaired_missing_heavy_atoms": repair_missing_heavy_atoms,
        "residues": rows,
        "suggested_template_assignments": phprep.template_assignments(rows),
        "software_versions": core.versions(),
    }


@app.post("/api/v1/ligands/microstates", dependencies=[Depends(require_api_key)])
def ligand_microstates(request: LigandMicrostatesRequest):
    try:
        states = phprep.enumerate_ligand_states(request.smiles, request.ph, request.max_states)
    except (ValueError, RuntimeError) as exc:
        domain_error(exc)
    return {
        "input_smiles": request.smiles,
        "pH": request.ph,
        "states": [
            {
                "index": state["index"],
                "smiles": state["smiles"],
                "formal_charge": state["formal_charge"],
            }
            for state in states
        ],
    }


@app.post("/api/v1/receptors/prepare", dependencies=[Depends(require_api_key)])
async def prepare_receptor(
    file: UploadFile = File(...),
    template_assignments: str = Form(""),
    repair_missing_heavy_atoms: bool = Form(True),
    heme_coordination_residue: str = Form(""),
):
    data = await read_upload(file)
    text = decode_text(data)
    suffix = Path(file.filename or "receptor.pdb").suffix or ".pdb"
    prep_id = uuid.uuid4().hex
    directory = API_ROOT / "preparations" / prep_id
    try:
        original_pdb = core.normalize_structure(text, suffix)
        pdb = original_pdb
        if repair_missing_heavy_atoms:
            pdb = core.repair_heavy_atoms(pdb)
        path = core.prepare_receptor(
            pdb, directory, template_assignments,
            heme_coordination_residue=heme_coordination_residue.strip() or None,
            heme_source_pdb=original_pdb,
        )
    except (ValueError, RuntimeError) as exc:
        domain_error(exc)
    manifest = {
        "id": prep_id,
        "kind": "receptor",
        "source_filename": safe_name(file.filename, "receptor.pdb"),
        "template_assignments": template_assignments,
        "repaired_missing_heavy_atoms": repair_missing_heavy_atoms,
        "software_versions": core.versions(),
    }
    (directory / "api_manifest.json").write_text(json.dumps(manifest, indent=2))
    return {
        **manifest,
        "download_url": f"/api/v1/preparations/{prep_id}/receptor.pdbqt",
        "prepared_filename": path.name,
    }


@app.post("/api/v1/ligands/prepare", dependencies=[Depends(require_api_key)])
def prepare_ligand(request: LigandPrepareRequest):
    from rdkit import Chem

    prep_id = uuid.uuid4().hex
    directory = API_ROOT / "preparations" / prep_id
    directory.mkdir(parents=True, exist_ok=True)
    try:
        mol = core.molecule(smiles=request.smiles)
        path = directory / "ligand.pdbqt"
        core.write_ligand(mol, path)
    except (ValueError, RuntimeError) as exc:
        domain_error(exc)
    manifest = {
        "id": prep_id,
        "kind": "ligand",
        "name": request.name,
        "smiles": request.smiles,
        "formal_charge": int(Chem.GetFormalCharge(mol)),
        "software_versions": core.versions(),
    }
    (directory / "api_manifest.json").write_text(json.dumps(manifest, indent=2))
    return {
        **manifest,
        "download_url": f"/api/v1/preparations/{prep_id}/ligand.pdbqt",
    }


@app.get("/api/v1/preparations/{prep_id}/{filename}", dependencies=[Depends(require_api_key)])
def download_preparation(prep_id: str, filename: str):
    if not re.fullmatch(r"[0-9a-f]{32}", prep_id):
        raise HTTPException(status_code=404, detail="Preparation not found.")
    allowed = {"receptor.pdbqt", "receptor_prepared.pdb", "ligand.pdbqt", "ligand.sdf", "api_manifest.json"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="File not found.")
    path = API_ROOT / "preparations" / prep_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    media = "application/json" if filename.endswith(".json") else "text/plain"
    return Response(path.read_bytes(), media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/v1/jobs/dock", dependencies=[Depends(require_api_key)])
async def submit_docking_job(
    receptor: UploadFile = File(...),
    ligands: list[UploadFile] = File(...),
    center: str = Form(...),
    size: str = Form(...),
    seeds: str = Form("[2026]"),
    exhaustiveness: int = Form(8),
    poses: int = Form(9),
    cpu: int = Form(2),
    reference: UploadFile | None = File(None),
):
    if not 1 <= len(ligands) <= 25:
        raise HTTPException(status_code=422, detail="Submit between 1 and 25 prepared ligand PDBQT files.")
    try:
        center_values = [float(x) for x in json.loads(center)]
        size_values = [float(x) for x in json.loads(size)]
        seed_values = [int(x) for x in json.loads(seeds)]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="center, size and seeds must be JSON arrays.") from exc

    submission = API_ROOT / "submissions" / uuid.uuid4().hex
    submission.mkdir(parents=True, exist_ok=True)

    receptor_path = submission / "receptor.pdbqt"
    receptor_path.write_bytes(await read_upload(receptor))

    ligand_records = []
    for index, upload in enumerate(ligands, 1):
        name = safe_name(upload.filename, f"ligand_{index:03d}.pdbqt")
        path = submission / f"ligand_{index:03d}.pdbqt"
        path.write_bytes(await read_upload(upload))
        ligand_records.append({"id": f"ligand_{index:03d}", "name": name, "path": str(path)})

    reference_path = None
    if reference is not None:
        reference_path = submission / "reference.sdf"
        reference_path.write_bytes(await read_upload(reference))

    config = {
        "receptor": str(receptor_path),
        "ligands": ligand_records,
        "reference": str(reference_path) if reference_path else None,
        "center": center_values,
        "size": size_values,
        "seeds": seed_values,
        "exhaustiveness": exhaustiveness,
        "poses": poses,
        "cpu": max(1, min(int(cpu), 8)),
    }
    try:
        core.validate_config(config)
        backend = compute_backend()
        handle = backend.submit(API_ROOT, config)
    except (ValueError, compute.ComputeBackendError) as exc:
        domain_error(exc)

    job_id = handle["job_id"]
    directory = API_ROOT / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    save_remote_job(directory, handle)
    (directory / "status.json").write_text(json.dumps({"state": "queued", "completed": 0}, indent=2))

    return {
        "job_id": job_id,
        "backend": handle["backend"],
        "state": "queued",
        "status_url": f"/api/v1/jobs/{job_id}",
        "results_url": f"/api/v1/jobs/{job_id}/results",
        "bundle_url": f"/api/v1/jobs/{job_id}/bundle",
    }


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def docking_status(job_id: str):
    directory = job_dir(job_id)
    remote = load_remote_job(directory)
    if remote is None:
        raise HTTPException(status_code=500, detail="GitHub Actions job metadata is missing.")
    status_data = sync_remote_job(directory, materialize=True)
    return {
        "job_id": job_id,
        "backend": "github-actions",
        **status_data,
    }


@app.get("/api/v1/jobs/{job_id}/results", dependencies=[Depends(require_api_key)])
def docking_results(job_id: str):
    directory = job_dir(job_id)
    remote = load_remote_job(directory)
    if remote is None:
        raise HTTPException(status_code=500, detail="GitHub Actions job metadata is missing.")
    status_data = sync_remote_job(directory, materialize=True)
    path = directory / "results.json"
    results = json.loads(path.read_text()) if path.is_file() else []
    return {
        "job_id": job_id,
        "backend": "github-actions",
        "status": status_data,
        "results": results,
    }


@app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_docking_job(job_id: str):
    directory = job_dir(job_id)
    remote = load_remote_job(directory)
    if remote is None:
        raise HTTPException(status_code=500, detail="GitHub Actions job metadata is missing.")
    backend = compute_backend()
    try:
        requested = backend.cancel(remote)
    except compute.ComputeBackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"job_id": job_id, "cancel_requested": bool(requested)}


@app.get("/api/v1/jobs/{job_id}/bundle", dependencies=[Depends(require_api_key)])
def docking_bundle(job_id: str):
    directory = job_dir(job_id)
    remote = load_remote_job(directory)
    if remote is None:
        raise HTTPException(status_code=500, detail="GitHub Actions job metadata is missing.")
    status_data = sync_remote_job(directory, materialize=True)
    if status_data.get("state") != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {status_data.get('state', 'not complete')}.")
    payload = core.bundle(directory)
    return Response(
        payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pandoc_job_{job_id}.zip"'},
    )


# The task-agent API is deliberately fail-closed and single-tenant in v1.
# Unlike the legacy API, agent routes cannot run with an unset key.
def require_agent_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
    if not API_KEY:
        raise HTTPException(status_code=503, detail="Agent API requires PANDOC_API_KEY configuration.")
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing agent API key.")


class AgentRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class StructureReview(BaseModel):
    chains: list[str] = Field(min_length=1)
    reference_residue: str = Field(min_length=1)
    retain: list[str] = Field(default_factory=list)
    alternates: dict[str, str] = Field(default_factory=dict)


class ChemistryReview(BaseModel):
    reference_smiles: str = Field(min_length=1, max_length=2000)
    template_assignments: str = Field(default="", max_length=6000)
    repair_missing_heavy_atoms: bool = True
    approved: bool = False


class RedockingReview(BaseModel):
    center: list[float] | None = None
    size: list[float] | None = None
    seeds: list[int] | None = None
    exhaustiveness: int = Field(default=8, ge=1, le=64)
    poses: int = Field(default=9, ge=1, le=20)
    cpu: int = Field(default=2, ge=1, le=8)
    approved: bool = False


def agent_error(error: task_agent.AgentError):
    raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/agent/tasks", dependencies=[Depends(require_agent_key)])
def agent_create(request: AgentRequest):
    try:
        plan = agent_planner.plan_request(
            request.instruction, api_key=os.environ.get("GROQ_API_KEY", ""),
            model=os.environ.get("GROQ_MODEL", agent_planner.MODEL),
        )
        return task_agent.create(API_ROOT, request.instruction, plan=plan)
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.get("/api/v1/agent/tasks/{task_id}", dependencies=[Depends(require_agent_key)])
def agent_read(task_id: str):
    try:
        return task_agent.describe(API_ROOT, task_id)
    except task_agent.AgentError as exc:
        agent_error(exc)


class TaskChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.get("/api/v1/agent/tasks/{task_id}/conversation", dependencies=[Depends(require_agent_key)])
def agent_conversation_history(task_id: str):
    try:
        task_agent.describe(API_ROOT, task_id)
        return {"task_id": task_id, "messages": agent_conversation.history(API_ROOT, task_id)}
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/chat", dependencies=[Depends(require_agent_key)])
def agent_chat(task_id: str, request: TaskChatRequest):
    try:
        answer = agent_conversation.respond(
            API_ROOT, task_id, request.message,
            api_key=os.environ.get("GROQ_API_KEY", ""),
            model=os.environ.get("GROQ_MODEL", agent_planner.MODEL),
            backend_factory=compute_backend,
        )
        return {
            "task_id": task_id, "answer": answer,
            "task": task_agent.describe(API_ROOT, task_id),
        }
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/select", dependencies=[Depends(require_agent_key)])
def agent_select(task_id: str, review: StructureReview):
    try:
        return task_agent.select_structure(
            API_ROOT, task_id, review.chains, review.reference_residue,
            review.retain, review.alternates
        )
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/protonation", dependencies=[Depends(require_agent_key)])
def agent_protonation(task_id: str):
    try:
        return task_agent.protonation(API_ROOT, task_id)
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/ligand-options", dependencies=[Depends(require_agent_key)])
def agent_ligand_options(task_id: str):
    try:
        return task_agent.ligand_options(API_ROOT, task_id)
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/prepare", dependencies=[Depends(require_agent_key)])
def agent_prepare(task_id: str, review: ChemistryReview):
    try:
        return task_agent.prepare(
            API_ROOT, task_id, reference_smiles=review.reference_smiles,
            template_assignments=review.template_assignments,
            repair_missing_heavy_atoms=review.repair_missing_heavy_atoms,
            approved=review.approved
        )
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/redock", dependencies=[Depends(require_agent_key)])
def agent_redock(task_id: str, review: RedockingReview):
    if not review.approved:
        raise HTTPException(status_code=422, detail="Explicit redocking approval is required.")
    try:
        return task_agent.submit_redocking(
            API_ROOT, task_id, backend=compute_backend(), approved=review.approved,
            center=review.center, size=review.size, seeds=review.seeds,
            exhaustiveness=review.exhaustiveness, poses=review.poses, cpu=review.cpu,
        )
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/refresh", dependencies=[Depends(require_agent_key)])
def agent_refresh(task_id: str):
    try:
        return task_agent.refresh(API_ROOT, task_id, compute_backend())
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.post("/api/v1/agent/tasks/{task_id}/cancel", dependencies=[Depends(require_agent_key)])
def agent_cancel(task_id: str):
    try:
        return task_agent.cancel(API_ROOT, task_id, compute_backend())
    except task_agent.AgentError as exc:
        agent_error(exc)


@app.get("/api/v1/agent/tasks/{task_id}/bundle", dependencies=[Depends(require_agent_key)])
def agent_bundle(task_id: str):
    try:
        content = task_agent.artifacts(API_ROOT, task_id)
    except task_agent.AgentError as exc:
        agent_error(exc)
    return Response(
        content, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pandoc_agent_{task_id}.zip"'},
    )
