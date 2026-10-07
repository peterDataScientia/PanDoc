from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pandoc import core, jobs, phprep


API_ROOT = Path(os.environ.get("PANDOC_API_DATA_DIR", "/tmp/pandoc_api")).resolve()
API_ROOT.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = int(os.environ.get("PANDOC_API_MAX_UPLOAD_MB", "25")) * 1024 * 1024
API_KEY = os.environ.get("PANDOC_API_KEY", "").strip()

app = FastAPI(
    title="PanDoc API",
    version="0.1.0",
    description="Programmatic access to PanDoc structure preparation and docking workflows.",
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
    return {"status": "ok", "service": "pandoc-api", "version": app.version}


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
):
    data = await read_upload(file)
    text = decode_text(data)
    suffix = Path(file.filename or "receptor.pdb").suffix or ".pdb"
    prep_id = uuid.uuid4().hex
    directory = API_ROOT / "preparations" / prep_id
    try:
        pdb = core.normalize_structure(text, suffix)
        if repair_missing_heavy_atoms:
            pdb = core.repair_heavy_atoms(pdb)
        path = core.prepare_receptor(pdb, directory, template_assignments)
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
        directory = Path(jobs.launch(API_ROOT, config))
    except ValueError as exc:
        domain_error(exc)
    return {
        "job_id": directory.name,
        "state": "queued",
        "status_url": f"/api/v1/jobs/{directory.name}",
        "results_url": f"/api/v1/jobs/{directory.name}/results",
        "bundle_url": f"/api/v1/jobs/{directory.name}/bundle",
    }


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def docking_status(job_id: str):
    directory = job_dir(job_id)
    return {"job_id": job_id, **jobs.status(directory)}


@app.get("/api/v1/jobs/{job_id}/results", dependencies=[Depends(require_api_key)])
def docking_results(job_id: str):
    directory = job_dir(job_id)
    status_data = jobs.status(directory)
    path = directory / "results.json"
    results = json.loads(path.read_text()) if path.is_file() else []
    return {"job_id": job_id, "status": status_data, "results": results}


@app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_docking_job(job_id: str):
    directory = job_dir(job_id)
    jobs.cancel(directory)
    return {"job_id": job_id, "cancel_requested": True}


@app.get("/api/v1/jobs/{job_id}/bundle", dependencies=[Depends(require_api_key)])
def docking_bundle(job_id: str):
    directory = job_dir(job_id)
    payload = core.bundle(directory)
    return Response(
        payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pandoc_job_{job_id}.zip"'},
    )
