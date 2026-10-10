"""PanDoc task agent: durable, approval-gated molecular redocking workflow.

The language parser proposes *identifiers*, never chemical decisions. Scientific
actions are performed by existing PanDoc functions; external calculation
submission requires an explicit approval and is never blindly retried.
Single-tenant pilot: deploy behind API authentication and a persistent disk.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid
import zipfile

from . import core, pdb_search, structure_checks, phprep

_LOCK = threading.RLock()
_TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_ISSUES = 25
STAGES = {
    "retrieving", "needs_pdb_id", "await_structure_review",
    "await_chemistry_review", "protonation_running", "preparing",
    "preparation_failed", "await_docking_approval", "submitting",
    "submission_uncertain", "running", "completed", "failed", "cancelled",
}


class AgentError(ValueError):
    """A controlled agent-state or scientific input validation failure."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def parse_request(message: str) -> dict:
    """Extract only explicitly typed PDB identifiers and a recorded pH."""
    text = str(message).strip()
    if not 1 <= len(text) <= 2000:
        raise AgentError("Describe the task in 1–2000 characters.")
    match = re.search(r"\bpdb(?:\s+(?:id|code|entry))?\s*[:=#-]?\s*([0-9][A-Za-z0-9]{3})\b", text, re.I)
    if not match:
        match = re.search(r"\b([0-9][A-Za-z0-9]{3})\b", text)
    pdb_id = match.group(1).upper() if match else None
    ph = re.search(r"\bpH\s*(?:(?:of|to|at|=|:)\s*)?(\d{1,2}(?:\.\d+)?)\b", text, re.I)
    ph_value = float(ph.group(1)) if ph else None
    if ph_value is not None and not 0 <= ph_value <= 14:
        raise AgentError("The requested pH must be between 0 and 14.")
    goal = ("redocking" if re.search(r"\bredock(?:ing)?\b|\bvalidat(?:e|ion)\b", text, re.I)
            else "preparation" if re.search(r"\bprepar(?:e|ation)\b", text, re.I)
            else "inspection")
    return {"pdb_id": pdb_id, "pH": ph_value, "goal": goal}


def _folder(root, task_id):
    if not _TASK_ID.fullmatch(str(task_id)):
        raise AgentError("Unknown agent task.")
    folder = Path(root) / "agent_tasks" / task_id
    if not (folder / "task.json").is_file():
        raise AgentError("Agent task was not found.")
    return folder


def _get(root, task_id):
    folder = _folder(root, task_id)
    try:
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AgentError("The agent task record is unreadable.") from exc
    return folder, task


def _write(folder, task, event=None, **details):
    if task.get("stage") not in STAGES:
        raise AgentError("Invalid task stage.")
    task["updated_utc"] = _now()
    if event:
        task.setdefault("audit", []).append({
            "at": task["updated_utc"], "action": event,
            "details": details,
        })
    tmp = folder / ("task." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(task, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(tmp, folder / "task.json")
    finally:
        tmp.unlink(missing_ok=True)


def _require(task, state):
    if task["stage"] != state:
        raise AgentError(
            f"Task stage is {task['stage']}; this action requires {state}."
        )


def _sha(contents):
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _inventory_response(folder, task):
    report = json.loads((folder / "inspection.json").read_text())
    issues = report["issues"]
    return {
        "id": task["id"],
        "stage": task["stage"],
        "pdb_id": task.get("pdb_id"),
        "goal": task["goal"],
        "requested_pH": task.get("pH"),
        "receptor_chains": report["receptor_chains"],
        "reference_candidates": report["reference_candidates"],
        "structural_issue_count": len(issues),
        "structural_issues": issues[:MAX_ISSUES],
        "structural_issues_truncated": len(issues) > MAX_ISSUES,
        "next_action": "Review receptor chains and a crystallographic reference ligand.",
    }


def create(root, instruction: str, plan=None):
    """Create one persisted task and perform read-only PDB retrieval/inspection."""
    parsed = parse_request(instruction)
    task_id = uuid.uuid4().hex
    folder = Path(root) / "agent_tasks" / task_id
    folder.mkdir(parents=True, exist_ok=False)
    task = {
        "id": task_id, "created_utc": _now(),
        "stage": "retrieving" if parsed["pdb_id"] else "needs_pdb_id",
        "instruction": instruction,
        "pdb_id": parsed["pdb_id"], "pH": parsed["pH"],
        "goal": parsed["goal"], "audit": [],
        "plan": plan if isinstance(plan, dict) else None,
    }
    _write(folder, task, "task_created", parsed=parsed)
    if parsed["pdb_id"] is None:
        return {
            "id": task_id, "stage": "needs_pdb_id",
            "message": "Please specify an exact four-character PDB ID; the agent will not guess a structure.",
        }
    try:
        cif, source = pdb_search.download(parsed["pdb_id"])
        pdb = core.normalize_structure(cif, ".cif")
        structure = structure_checks.inventory(pdb)
        checks = structure_checks.check(pdb, raw=True)
        references = [
            {"residue": row["residue"], "component": row["name"], "heavy_atoms": row["heavy_atoms"]}
            for row in structure["components"] if row["kind"] == "Other component"
        ]
        chains = sorted({row["chain"] for row in structure["components"] if row["kind"] == "Protein"})
        if not chains:
            raise AgentError("Downloaded structure contains no recognized protein chains.")
        (folder / "structure.pdb").write_text(pdb, encoding="utf-8")
        report = {
            "receptor_chains": chains, "reference_candidates": references,
            "components": structure["components"], "alternates": structure["alternates"],
            "connections": structure["connections"], "issues": checks["issues"],
            "atom_count": checks["atom_count"], "sha256": _sha(pdb),
            "source": source,
        }
        (folder / "inspection.json").write_text(json.dumps(report, indent=2))
        task["source_sha256"] = report["sha256"]
        task["stage"] = "await_structure_review"
        _write(folder, task, "structure_inspected", pdb_id=parsed["pdb_id"],
               residues=len(report["components"]), issues=len(report["issues"]))
    except (OSError, ValueError, RuntimeError) as exc:
        task["stage"] = "failed"
        task["failure"] = str(exc)[:500]
        _write(folder, task, "inspection_failed", reason=task["failure"])
        raise AgentError("PDB retrieval or structural inspection failed: " + str(exc)[:350]) from exc
    return _inventory_response(folder, task)


def select_structure(root, task_id, chains, reference_residue, retain=None, alternates=None):
    """User-reviewed receptor/reference selection; no chemical state inferred."""
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_structure_review")
        report = json.loads((folder / "inspection.json").read_text())
        selected_chains = list(dict.fromkeys(chains or []))
        if not selected_chains or not set(selected_chains).issubset(set(report["receptor_chains"])):
            raise AgentError("Select one or more available receptor protein chains.")
        ref = next((row for row in report["reference_candidates"]
                    if row["residue"] == reference_residue), None)
        if ref is None:
            raise AgentError("Choose a listed crystallographic reference ligand.")
        retain = list(dict.fromkeys(retain or []))
        protein_ids = {
            row["residue"] for row in report["components"]
            if row["kind"] == "Protein" and row["chain"] in selected_chains
        }
        allowed_retained = {
            row["residue"] for row in report["components"]
            if row["kind"] in ("Water", "Metal / ion", "Other component")
            and row["residue"] != reference_residue
        }
        if not set(retain).issubset(allowed_retained):
            raise AgentError("Retained components must come from the reviewed structure and exclude the reference.")
        # A covalently linked reference cannot be safely separated into a
        # rigid receptor + ordinary freely-docked ligand. Require a manually
        # curated protocol instead of silently dropping the LINK record.
        for connection in report.get("connections", []):
            if not connection.startswith("LINK  "):
                continue
            endpoints = [
                f"{connection[21:22].strip() or '_'}:{connection[22:26].strip()}{connection[26:27].strip()}:{connection[17:20].strip()}",
                f"{connection[51:52].strip() or '_'}:{connection[52:56].strip()}{connection[56:57].strip()}:{connection[47:50].strip()}",
            ]
            if reference_residue in endpoints:
                raise AgentError(
                    "The crystallographic reference has a recorded covalent/coordination LINK. "
                    "Generic noncovalent redocking is blocked; review the linked chemistry manually."
                )
        alternatives = {r["residue"]: {opt["label"] for opt in r["options"]}
                        for r in report.get("alternates", [])}
        for key, val in (alternates or {}).items():
            if key not in alternatives or val not in alternatives[key]:
                raise AgentError("Invalid alternate-conformation assignment.")
        pdb = (folder / "structure.pdb").read_text()
        receptor = core.select(pdb, protein_ids | set(retain), per_residue=alternates)
        reference = core.select(pdb, {reference_residue}, per_residue=alternates)
        problems = structure_checks.check(receptor)["issues"]
        errors = [x for x in problems if x["severity"] == "Error"]
        if errors:
            raise AgentError(
                f"Receptor selection has {len(errors)} blocking coordinate error(s); "
                "review/correct them before preparation."
            )
        if any(row["name"] == "HEM" for row in report["components"]
               if row["residue"] in retain):
            raise AgentError(
                "HEM requires curated Fe-coordination review; automatic task-agent "
                "preparation is not enabled for retained HEM."
            )
        (folder / "selected_receptor.pdb").write_text(receptor)
        (folder / "crystal_reference.pdb").write_text(reference)
        choices = {"chains": selected_chains, "reference_residue": reference_residue,
                   "reference_component": ref["component"], "retained": retain,
                   "alternates": alternates or {},
                   "receptor_sha256": _sha(receptor), "reference_sha256": _sha(reference)}
        (folder / "selection.json").write_text(json.dumps(choices, indent=2))
        task["selection"] = choices
        task["stage"] = "await_chemistry_review"
        _write(folder, task, "selection_approved",
               chains=selected_chains, reference=reference_residue, retained=retain)
        return {"id": task_id, "stage": task["stage"],
                "selection": choices, "coordinate_warning_count": len(problems),
                "next_action": "Review PROPKA proposals and verify the crystal ligand SMILES before preparation."}


def revise_intent(root, task_id, *, goal, ph=None):
    """Upgrade an existing inspected task without duplicating PDB retrieval.

    Changing pH invalidates prior prediction proposals. Once preparation or
    docking has started, the task cannot change its chemical interpretation.
    """
    if goal not in ("preparation", "redocking"):
        raise AgentError("Only preparation or redocking may extend this task.")
    if ph is not None and (not isinstance(ph, (float, int)) or not 0 <= ph <= 14):
        raise AgentError("Preparation pH must be between 0 and 14.")
    order = {"inspection": 0, "preparation": 1, "redocking": 2}
    with _LOCK:
        folder, task = _get(root, task_id)
        if task["stage"] not in ("await_structure_review", "await_chemistry_review"):
            raise AgentError("Chemical and docking plans cannot be changed after preparation.")
        before = (task["goal"], task.get("pH"))
        task["goal"] = max((task["goal"], goal), key=lambda x: order.get(x, -1))
        if ph is not None and ph != task.get("pH"):
            task["pH"] = float(ph)
            for file in ("protonation.json", "ligand_options.json"):
                (folder / file).unlink(missing_ok=True)
        if (task["goal"], task.get("pH")) != before:
            if isinstance(task.get("plan"), dict):
                task["plan"]["goal"] = task["goal"]
                task["plan"]["pH"] = task.get("pH")
                task["plan"]["summary"] = (
                    f"Continue PDB {task.get('pdb_id')} toward {task['goal']} "
                    f"with preparation pH {task.get('pH') if task.get('pH') is not None else 'to be specified'}; "
                    "scientific component and chemical-state reviews remain mandatory."
                )
                task["plan"]["source"] = "explicit_user_followup"
            _write(folder, task, "intent_revised", previous_goal=before[0],
                   new_goal=task["goal"], previous_pH=before[1], new_pH=task["pH"])
        return {"id": task_id, "stage": task["stage"],
                "goal": task["goal"], "pH": task.get("pH")}


def protonation(root, task_id):
    """Run PROPKA, record its proposals without assigning them automatically."""
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_chemistry_review")
        if task.get("pH") is None:
            raise AgentError("Specify intended preparation pH before PROPKA prediction.")
        previous = folder / "protonation.json"
        if previous.is_file():
            data = json.loads(previous.read_text())
            return {"stage": task["stage"], "proposal": data, "cached": True}
        task["stage"] = "protonation_running"
        _write(folder, task, "protonation_started", pH=task["pH"])
    try:
        pdb = (folder / "selected_receptor.pdb").read_text()
        rows, raw = phprep.predict_protein_states(pdb, folder / "propka", task["pH"])
        proposal = {
            "pH": task["pH"], "residues": rows,
            "proposed_assignments": phprep.template_assignments(rows),
            "review_required": [r["residue"] for r in rows if r["review_required"]],
            "warning": "Predictions require scientific review; no protonation states were automatically accepted.",
            "raw_output_sha256": _sha(raw),
        }
    except (ValueError, OSError, RuntimeError) as exc:
        with _LOCK:
            folder, task = _get(root, task_id)
            task["stage"] = "await_chemistry_review"
            _write(folder, task, "protonation_failed", reason=str(exc)[:300])
        raise AgentError("PROPKA prediction failed; review preparation manually: " + str(exc)[:200]) from exc
    with _LOCK:
        folder, task = _get(root, task_id)
        (folder / "protonation.json").write_text(json.dumps(proposal, indent=2))
        task["stage"] = "await_chemistry_review"
        _write(folder, task, "protonation_ready", residue_count=len(rows))
    return {"stage": "await_chemistry_review", "proposal": proposal, "cached": False}


def ligand_options(root, task_id):
    """Retrieve CCD chemical identity, then propose pH-aware ligand microstates.

    Chemical definitions are suggestions only. Heavy-atom composition is checked
    against the selected deposited ligand; no state is automatically accepted.
    """
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_chemistry_review")
        existing = folder / "ligand_options.json"
        if existing.is_file():
            return {"stage": task["stage"], "options": json.loads(existing.read_text()),
                    "cached": True}
        component = task["selection"]["reference_component"]
        reference = (folder / "crystal_reference.pdb").read_text()

    try:
        ccd = core.fetch_ccd(component)
        comparison = core.ligand_comparison(reference, ccd["smiles"])
        states = []
        issue = None
        if task.get("pH") is not None and comparison.get("valid"):
            try:
                raw = phprep.enumerate_ligand_states(ccd["smiles"], task["pH"], 16)
                states = [
                    {"index": x["index"], "smiles": x["smiles"],
                     "formal_charge": x["formal_charge"]}
                    for x in raw
                ]
            except (OSError, ValueError, RuntimeError) as exc:
                issue = "Ligand microstate enumeration failed; review chemistry manually: " + str(exc)[:180]
        proposal = {
            "component": component, "ccd_smiles": ccd["smiles"],
            "ccd_source": ccd.get("source"),
            "heavy_atom_comparison": comparison,
            "pH": task.get("pH"), "microstates": states,
            "warning": issue or (
                "CCD chemistry and MolScrub states are candidates only. "
                "Approve isomeric SMILES and chemical state before preparing."
            ),
        }
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgentError("CCD chemistry lookup failed; enter reviewed reference SMILES manually: " +
                         str(exc)[:240]) from exc

    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_chemistry_review")
        (folder / "ligand_options.json").write_text(json.dumps(proposal, indent=2))
        _write(folder, task, "ligand_options_collected",
               component=component, states=len(proposal["microstates"]))
    return {"stage": "await_chemistry_review", "options": proposal, "cached": False}


def prepare(root, task_id, *, reference_smiles, template_assignments="",
            repair_missing_heavy_atoms=True, approved=False):
    """Require explicit chemistry acceptance; preserve deposited reference pose."""
    if approved is not True:
        raise AgentError("Explicit researcher approval of ligand identity and chemical states is required.")
    if not str(reference_smiles).strip():
        raise AgentError("The reviewed reference-ligand isomeric SMILES is required.")
    if len(reference_smiles) > 2000 or len(template_assignments) > 6000:
        raise AgentError("Chemical-state input exceeds limits.")
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_chemistry_review")
        receptor = (folder / "selected_receptor.pdb").read_text()
        reference = (folder / "crystal_reference.pdb").read_text()
        comparison = core.ligand_comparison(reference, reference_smiles)
        if not comparison["valid"]:
            raise AgentError("The crystal reference and SMILES do not match: " + comparison["message"])
        mol = core.reference_from_pdb(reference, reference_smiles)
        task["stage"] = "preparing"
        _write(folder, task, "chemistry_approved", pH=task.get("pH"),
               repair=repair_missing_heavy_atoms, reference_component=task["selection"]["reference_component"])
    try:
        prep_dir = folder / "prepared"
        prep_dir.mkdir(exist_ok=True)
        prepared_input = core.repair_heavy_atoms(receptor) if repair_missing_heavy_atoms else receptor
        path = core.prepare_receptor(prepared_input, prep_dir,
                                     template_assignments=template_assignments,
                                     heme_source_pdb=receptor)
        core.write_ligand(mol, prep_dir / "reference.pdbqt")
        prepared_pdb = (prep_dir / "receptor_prepared.pdb").read_text()
        issues = structure_checks.check(prepared_pdb)["issues"]
        blocking = [x for x in issues if x["severity"] == "Error"]
        if blocking:
            raise AgentError(f"Prepared receptor has {len(blocking)} blocking coordinate errors.")
        center, size = core.box(reference, padding=5.0)
        chemistry = {
            "pH": task.get("pH"),
            "reference_smiles": reference_smiles,
            "template_assignments": template_assignments,
            "repair_missing_heavy_atoms": bool(repair_missing_heavy_atoms),
            "reference_crystal_sha256": task["selection"]["reference_sha256"],
            "receptor_prepared_sha256": _sha(prepared_pdb),
            "prepared_pdbqt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "review_approved": True,
            "quality_warnings": issues[:MAX_ISSUES],
        }
        (folder / "chemistry.json").write_text(json.dumps(chemistry, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        with _LOCK:
            folder, task = _get(root, task_id)
            task["stage"] = "preparation_failed"
            task["failure"] = str(exc)[:400]
            _write(folder, task, "preparation_failed", reason=task["failure"])
        raise AgentError("Preparation failed without accepting the output: " + str(exc)[:300]) from exc
    with _LOCK:
        folder, task = _get(root, task_id)
        task["stage"] = "await_docking_approval"
        task["docking_proposal"] = {
            "center_A": [round(float(v), 3) for v in center],
            "size_A": [round(float(v), 3) for v in size],
            "exhaustiveness": 8, "poses": 9,
            "seeds": [2026, 2027, 2028], "cpu": 2,
        }
        task.pop("failure", None)
        _write(folder, task, "preparation_completed",
               crystal_reference_preserved=True, receptor_sha256=chemistry["receptor_prepared_sha256"])
    return {"id": task_id, "stage": "await_docking_approval",
            "docking_proposal": task["docking_proposal"],
            "next_action": "Review box and search settings, then explicitly approve submission."}


def submit_redocking(root, task_id, *, approved, backend,
                     center=None, size=None, exhaustiveness=8,
                     poses=9, seeds=None, cpu=2):
    if approved is not True:
        raise AgentError("Docking submission needs explicit approval.")
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "await_docking_approval")
        proposal = task["docking_proposal"]
        config = {
            "receptor": str(folder / "prepared" / "receptor.pdbqt"),
            "reference": str(folder / "prepared" / "reference.sdf"),
            "ligands": [{
                "id": "reference", "name": "Reference ligand",
                "path": str(folder / "prepared" / "reference.pdbqt"),
            }],
            "center": center if center is not None else proposal["center_A"],
            "size": size if size is not None else proposal["size_A"],
            "seeds": seeds if seeds is not None else proposal["seeds"],
            "exhaustiveness": exhaustiveness, "poses": poses,
            "cpu": cpu,
        }
        core.validate_config(config)
        if not 1 <= int(cpu) <= 8:
            raise AgentError("CPU allocation must be between 1 and 8.")
        for name in ("receptor", "reference"):
            if not Path(config[name]).is_file():
                raise AgentError("Prepared input is missing: " + name)
        if not Path(config["ligands"][0]["path"]).is_file():
            raise AgentError("Prepared reference PDBQT is missing.")
        # Commit the state BEFORE the external side effect. If the response
        # disappears, never blindly resubmit and risk duplicate billing/compute.
        task["stage"] = "submitting"
        task["submission_nonce"] = uuid.uuid4().hex
        (folder / "docking_config.json").write_text(json.dumps(config, indent=2))
        _write(folder, task, "docking_submission_approved",
               nonce=task["submission_nonce"], seeds=config["seeds"])
    try:
        remote = backend.submit(folder, config)
        if not isinstance(remote, dict) or not remote.get("job_id"):
            raise RuntimeError("Compute backend returned no durable job identifier.")
    except Exception as exc:
        with _LOCK:
            folder, task = _get(root, task_id)
            task["stage"] = "submission_uncertain"
            task["failure"] = str(exc)[:300]
            _write(folder, task, "submission_uncertain",
                   note="Do not retry automatically; reconcile the backend job first.")
        raise AgentError("Submission outcome is uncertain; no automatic retry will be attempted.") from exc
    with _LOCK:
        folder, task = _get(root, task_id)
        task["remote_handle"] = remote
        task["stage"] = "running"
        _write(folder, task, "redocking_submitted",
               job_id=remote["job_id"], backend=remote.get("backend"))
    return {"id": task_id, "stage": "running",
            "job_id": remote["job_id"], "backend": remote.get("backend")}


def refresh(root, task_id, backend):
    """Check an existing job; only collect validated artifacts on completion."""
    with _LOCK:
        folder, task = _get(root, task_id)
        if task["stage"] in ("completed", "failed", "cancelled"):
            return describe(root, task_id)
        _require(task, "running")
        handle = task["remote_handle"]
    try:
        remote = backend.status(handle)
        state = remote.get("state", "running")
        if state == "completed":
            out = folder / "results"
            out.mkdir(exist_ok=True)
            backend.materialize(handle, out)
            if not (out / "results.json").is_file():
                raise AgentError("Compute finished without result rows.")
            from . import assistant_tools
            summary = assistant_tools.dispatch("get_calculation_summary", {}, {"job": out})
            if not summary.get("available"):
                raise AgentError("Saved poses could not be inspected.")
            (folder / "redocking_summary.json").write_text(json.dumps(summary, indent=2))
            try:
                backend.cleanup(handle)
            except Exception:
                pass
    except (OSError, ValueError, RuntimeError) as exc:
        # Transient remote connectivity failures do not alter persisted state.
        return {"id": task_id, "stage": "running", "status_error": str(exc)[:250],
                "message": "Remote job status or artifacts could not be retrieved."}
    with _LOCK:
        folder, task = _get(root, task_id)
        if state == "completed":
            task["stage"] = "completed"
            task["summary"] = summary
            _write(folder, task, "redocking_completed", saved_poses=summary["saved_pose_count"])
        elif state in ("failed", "cancelled"):
            task["stage"] = state
            task["failure"] = str(remote.get("error", "Compute backend reported " + state))[:300]
            _write(folder, task, "redocking_" + state)
        else:
            task["latest_remote_state"] = {k: remote[k] for k in ("state", "completed", "total")
                                           if k in remote}
            _write(folder, task)
    return describe(root, task_id)


def cancel(root, task_id, backend):
    with _LOCK:
        folder, task = _get(root, task_id)
        _require(task, "running")
        handle = task["remote_handle"]
    stopped = bool(backend.cancel(handle))
    with _LOCK:
        folder, task = _get(root, task_id)
        _write(folder, task, "cancel_requested", acknowledged=stopped)
    return {"id": task_id, "stage": task["stage"], "cancel_requested": stopped}


def describe(root, task_id):
    with _LOCK:
        folder, task = _get(root, task_id)
        data = {
            "id": task["id"], "stage": task["stage"], "goal": task["goal"],
            "pdb_id": task.get("pdb_id"), "pH": task.get("pH"),
            "created_utc": task["created_utc"], "updated_utc": task["updated_utc"],
            "plan": task.get("plan"),
            "audit_count": len(task["audit"]),
        }
        if task["stage"] == "needs_pdb_id":
            data["next_action"] = "Provide the four-character PDB ID."
        if task["stage"] == "await_structure_review":
            data.update(_inventory_response(folder, task))
        if "selection" in task:
            data["selection"] = {k: task["selection"][k]
                                 for k in ("chains", "reference_residue", "reference_component", "retained")}
        if task["stage"] == "await_chemistry_review":
            path = folder / "protonation.json"
            data["protonation_available"] = path.is_file()
            data["ligand_options_available"] = (folder / "ligand_options.json").is_file()
            data["next_action"] = "Review crystal reference chemistry and residue states."
        if task.get("docking_proposal"):
            data["docking_proposal"] = task["docking_proposal"]
        if task.get("remote_handle"):
            data["job_id"] = task["remote_handle"].get("job_id")
            data["compute_backend"] = task["remote_handle"].get("backend")
        if task.get("latest_remote_state"):
            data["remote_state"] = task["latest_remote_state"]
        if task.get("summary"):
            data["summary"] = task["summary"]
        if task.get("failure"):
            data["failure"] = task["failure"]
        return data


def artifacts(root, task_id):
    """Create a bounded reproducibility bundle with task-specific files only."""
    folder, task = _get(root, task_id)
    if task["stage"] != "completed":
        raise AgentError("Results are available only after the task completes.")
    selected = [
        "inspection.json", "selection.json", "protonation.json", "ligand_options.json", "chemistry.json",
        "docking_config.json", "redocking_summary.json",
        "selected_receptor.pdb", "crystal_reference.pdb",
        "prepared/receptor.pdbqt", "prepared/receptor_prepared.pdb",
        "prepared/reference.pdbqt", "prepared/reference.sdf",
        "prepared/preparation.log", "results/results.json", "results/status.json",
    ]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agent_task.json", json.dumps({
            "id": task["id"], "pdb_id": task["pdb_id"], "pH": task["pH"],
            "audit": task["audit"], "stage": task["stage"],
            "software_versions": core.versions(),
        }, indent=2))
        for rel in selected:
            path = folder / rel
            if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
                archive.write(path, rel)
    return stream.getvalue()
