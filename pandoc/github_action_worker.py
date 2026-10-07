from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from . import phprep
from .github_compute import decrypt_payload
from .worker import run as run_worker


def _safe_extract(archive: zipfile.ZipFile, target: Path):
    root = target.resolve()
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        if root not in destination.parents and destination != root:
            raise RuntimeError("Unsafe path in encrypted PanDoc payload.")
    archive.extractall(root)


def _run_docking(root: Path, result_dir: Path):
    config = json.loads((root / "config.json").read_text())
    config["receptor"] = str((root / config["receptor"]).resolve())
    for ligand in config.get("ligands", []):
        ligand["path"] = str((root / ligand["path"]).resolve())
    if config.get("reference"):
        config["reference"] = str((root / config["reference"]).resolve())

    (result_dir / "config.json").write_text(json.dumps(config, indent=2))
    log_path = result_dir / "worker.log"
    with log_path.open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        run_worker(result_dir)

    status_path = result_dir / "status.json"
    if not status_path.is_file():
        raise RuntimeError("PanDoc worker did not write a status file.")
    status = json.loads(status_path.read_text())
    if status.get("state") == "failed":
        raise RuntimeError("PanDoc docking failed. Download the artifact and inspect worker.log.")
    if status.get("state") == "cancelled":
        raise RuntimeError("PanDoc docking was cancelled.")


def _run_ligand_microstates(root: Path, result_dir: Path):
    request = json.loads((root / "request.json").read_text())
    smiles = str(request["smiles"])
    ph = float(request["ph"])
    max_states = int(request.get("max_states", 16))

    states = phprep.enumerate_ligand_states(smiles, ph, max_states)
    payload = {
        "input_smiles": smiles,
        "pH": ph,
        "states": [
            {
                "index": state["index"],
                "smiles": state["smiles"],
                "formal_charge": state["formal_charge"],
            }
            for state in states
        ],
    }
    (result_dir / "results.json").write_text(json.dumps(payload, indent=2))
    (result_dir / "status.json").write_text(
        json.dumps({"state": "completed", "completed": 1, "total": 1}, indent=2)
    )


def main(job_id: str, operation: str = "dock"):
    key = os.environ.get("PANDOC_JOB_KEY", "").strip()
    if not key:
        raise RuntimeError("PANDOC_JOB_KEY is not configured in repository Actions secrets.")

    payload_path = Path(".pandoc_jobs") / f"{job_id}.bin"
    if not payload_path.is_file():
        raise RuntimeError("Encrypted PanDoc job payload was not found.")

    result_dir = Path(".pandoc_action_runs") / job_id
    result_dir.mkdir(parents=True, exist_ok=True)

    decrypted = decrypt_payload(payload_path.read_bytes(), key)
    with tempfile.TemporaryDirectory(prefix="pandoc-action-") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
            _safe_extract(archive, root)

        if operation == "dock":
            _run_docking(root, result_dir)
        elif operation == "ligand-microstates":
            _run_ligand_microstates(root, result_dir)
        else:
            raise RuntimeError(f"Unsupported PanDoc Actions operation: {operation}")

    print(f"PanDoc {operation} job {job_id[:8]} completed.")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: python -m pandoc.github_action_worker JOB_ID [OPERATION]")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "dock")
