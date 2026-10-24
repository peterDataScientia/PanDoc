"""Resumable session-local preparation of large SMILES CSV libraries for GPU docking.

No docking is performed here. Each valid molecule becomes its own PDBQT file.
The checkpoint survives Streamlit reruns, but not deletion of the temp session.
"""
from __future__ import annotations
import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

MAX_LIGANDS = 10_000
BATCH_SIZE = 12


def parse_csv(data: bytes, id_column: str, smiles_column: str) -> list[dict]:
    if len(data) > 12_000_000:
        raise ValueError("CSV exceeds the 12 MB safety limit. Supply a compact ID/SMILES CSV.")
    try:
        stream = io.StringIO(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(stream)
    if not reader.fieldnames or id_column not in reader.fieldnames or smiles_column not in reader.fieldnames:
        raise ValueError("Select the ID and SMILES columns from the CSV header.")
    rows = []
    for number, row in enumerate(reader, 2):
        if not any(str(v or "").strip() for v in row.values()):
            continue
        if len(rows) >= MAX_LIGANDS:
            raise ValueError("Maximum 10,000 compounds per CSV; no input was prepared.")
        name = str(row.get(id_column) or "").strip() or f"row_{number}"
        smiles = str(row.get(smiles_column) or "").strip()
        rows.append({"id": name, "smiles": smiles, "csv_line": number})
    if not rows:
        raise ValueError("No compounds found.")
    return rows


def start(directory: Path, rows: list[dict], ph: float) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "checkpoint.json").exists():
        raise ValueError("Preparation directory already contains a job.")
    (directory / "pdbqt").mkdir(exist_ok=True)
    (directory / "input.json").write_text(json.dumps(rows), encoding="utf-8")
    state = {"total": len(rows), "completed": 0, "success": 0, "failed": 0,
             "pH_context": float(ph), "status": "running", "started_at": time.time(),
             "protonation": "SMILES as supplied; no automatic microstate enumeration",
             "files": {}}
    save(directory, state)
    return state


def save(directory: Path, state: dict):
    destination = directory / "checkpoint.json"
    temporary = directory / "checkpoint.tmp"
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(destination)


def load(directory: Path) -> dict:
    return json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))


def advance(directory: Path, batch_size: int = BATCH_SIZE) -> dict:
    from . import core
    directory = Path(directory)
    state = load(directory)
    if state["status"] != "running":
        return state
    rows = json.loads((directory / "input.json").read_text(encoding="utf-8"))
    stop = min(len(rows), state["completed"] + max(1, min(int(batch_size), 40)))
    successes = directory / "success.jsonl"
    failures = directory / "failed.jsonl"
    with successes.open("a", encoding="utf-8") as ok, failures.open("a", encoding="utf-8") as bad:
        for index in range(state["completed"], stop):
            item = rows[index]
            name = f"ligand_{index+1:06d}"
            output = directory / "pdbqt" / (name + ".pdbqt")
            try:
                if not item["smiles"]:
                    raise ValueError("Missing SMILES")
                mol = core.molecule(smiles=item["smiles"])
                core.write_ligand(mol, output)
                if not output.exists() or output.stat().st_size < 100:
                    raise RuntimeError("PDBQT was not produced")
                # SDF is not needed for docking-only GPU transfer.
                output.with_suffix(".sdf").unlink(missing_ok=True)
                ok.write(json.dumps({"id": item["id"], "file": "pdbqt/" + output.name,
                                     "smiles": item["smiles"], "csv_line": item["csv_line"]}) + "\n")
                state["success"] += 1
            except Exception as exc:
                output.unlink(missing_ok=True)
                output.with_suffix(".sdf").unlink(missing_ok=True)
                bad.write(json.dumps({"id": item["id"], "smiles": item["smiles"],
                                      "csv_line": item["csv_line"], "error": str(exc)[:500]}) + "\n")
                state["failed"] += 1
            state["completed"] = index + 1
    if state["completed"] >= state["total"]:
        state["status"] = "completed"
        state["finished_at"] = time.time()
    save(directory, state)
    return state


def build_zip(directory: Path) -> Path:
    directory = Path(directory)
    state = load(directory)
    if state["status"] != "completed":
        raise ValueError("Preparation is not complete.")
    result = directory / "PanDoc_GPU_ready_ligands.zip"
    if result.exists():
        return result
    def write_csv(source, dest, fields):
        rows = [json.loads(s) for s in source.read_text(encoding="utf-8").splitlines() if s.strip()]
        mem = io.StringIO()
        writer = csv.DictWriter(mem, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        dest.writestr(source.stem + ".csv", mem.getvalue())
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for ligand in sorted((directory / "pdbqt").glob("*.pdbqt")):
            archive.write(ligand, "pdbqt/" + ligand.name)
        write_csv(directory / "success.jsonl", archive, ["id", "file", "smiles", "csv_line"])
        write_csv(directory / "failed.jsonl", archive, ["id", "smiles", "csv_line", "error"])
        archive.writestr("preparation_manifest.json", json.dumps(state, indent=2))
    return result
