"""Bounded, read-only scientific evidence tools for the PanDoc Groq assistant.

Only trusted PanDoc session state selects a calculation or structure. LLM
arguments cannot specify file paths, execute code, or start/modify jobs.
No molecular coordinates, raw PDB/SDF/PDBQT, ligand names, or filenames are
sent to the model. Tool outputs are evidence, not instructions.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import re

MAX_RESULT_BYTES = 20 * 1024 * 1024
MAX_ROWS = 30
MAX_ISSUES = 30

def _tool(name, description, properties=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": [], "additionalProperties": False},
    }}

SCHEMAS = [
    _tool("get_calculation_summary",
          "Summarize ALL saved poses of the currently selected PanDoc docking/redocking calculation. "
          "Reports exact counts, best scores, top-ranked and best-pose RMSD recovery per anonymized compound; "
          "a successful redocking RMSD does not validate binding affinity."),
    _tool("get_calculation_rows",
          "Read a bounded page of actual saved docking poses, with anonymous compound labels. "
          "Use to verify claims about scores, RMSD, seeds and ranks. Never infer unprovided interactions.",
          {"sort_by": {"type": "string", "enum": ["file_order", "score", "rmsd"]},
           "offset": {"type": "integer", "minimum": 0},
           "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}}),
    _tool("get_structure_evidence",
          "Read recorded structure screening and preparation changes for the loaded structure. "
          "Coordinate checks are preliminary and not a chemical validation."),
    _tool("inspect_residue",
          "Read the atom names/elements of ONE residue in the currently selected first-model structure, "
          "without sending molecular coordinates. A residue identifier looks like A:437:CYS.",
          {"residue": {"type": "string"}}),
    _tool("get_job_provenance",
          "Read recorded job state, redocking settings, reviewed preparation metadata and version provenance. "
          "Does not alter job state or inspect hidden secrets."),
]


def _clean(value, maximum=180):
    text = str(value)
    text = re.sub(r"gsk_[A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(r"(?:/[\w .-]+){2,}|[A-Za-z]:\\[^\s]+", "[local path]", text)
    return text[:maximum]


def _safe_fields(obj, keys):
    if not isinstance(obj, dict):
        return {}
    return {k: _clean(obj[k]) if isinstance(obj[k], str) else obj[k]
            for k in keys if k in obj and isinstance(obj[k], (str, int, float, bool, type(None)))}


def _load_job_file(evidence, name):
    job = evidence.get("job")
    if job is None:
        return None
    path = Path(job) / name  # name is hardcoded by the dispatcher, never model-supplied
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise ValueError("Selected calculation evidence is too large to inspect safely.")
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(evidence):
    raw = _load_job_file(evidence, "results.json")
    if raw is None:
        return None, {}
    if not isinstance(raw, list):
        raise ValueError("Saved results do not contain a list of poses.")
    aliases = {}
    result = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            continue
        identifier = str(row.get("ligand_id", row.get("ligand", "unknown")))
        compound = aliases.setdefault(identifier, "Compound " + str(len(aliases) + 1))
        data = {"row_index": index, "compound": compound}
        for key in ("seed", "rank"):
            try:
                value = int(row[key])
                if value >= 0:
                    data[key] = value
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
        for key in ("score_kcal_mol", "reference_rmsd_A"):
            try:
                value = float(row[key])
                if math.isfinite(value) and (key != "reference_rmsd_A" or value >= 0):
                    data[key] = round(value, 5)
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
        result.append(data)
    return result, aliases


def _summary(evidence):
    rows, aliases = _rows(evidence)
    if rows is None:
        return {"available": False, "reason": "No saved results for selected calculation."}
    groups = defaultdict(list)
    for row in rows:
        groups[row["compound"]].append(row)
    items = []
    for compound, group in groups.items():
        scores = [r["score_kcal_mol"] for r in group if "score_kcal_mol" in r]
        rmsds = [r["reference_rmsd_A"] for r in group if "reference_rmsd_A" in r]
        seeds = defaultdict(list)
        for row in group:
            if "seed" in row:
                seeds[row["seed"]].append(row)
        valid_reference_seeds = {
            seed: group_rows for seed, group_rows in seeds.items()
            if any("reference_rmsd_A" in r for r in group_rows)
        }
        recovered = sum(
            min(r["reference_rmsd_A"] for r in group_rows if "reference_rmsd_A" in r) <= 2.0
            for group_rows in valid_reference_seeds.values()
        )
        top_recovered = sum(
            any(r.get("rank") == 1 and r.get("reference_rmsd_A", math.inf) <= 2.0
                for r in group_rows)
            for group_rows in valid_reference_seeds.values()
        )
        items.append({
            "compound": compound, "poses": len(group), "unique_seeds": len(seeds),
            "best_vina_score_kcal_mol": min(scores) if scores else None,
            "best_reference_rmsd_A": min(rmsds) if rmsds else None,
            "reference_seeds_assessed": len(valid_reference_seeds),
            "best_pose_recovery_le_2A_seeds": recovered,
            "top_rank_recovery_le_2A_seeds": top_recovered,
        })
    return {
        "available": True, "saved_pose_count": len(rows),
        "analyzed_all_saved_rows": True, "compounds": items[:100],
        "compound_count": len(items), "compound_table_truncated": len(items) > 100,
        "recovery_threshold_A": 2.0,
        "scope": "Saved poses only, not unsaved searches; scores are not measured affinities. "
                 "Seed-level recovery uses available reference RMSD and rank 1.",
    }


def _paged(evidence, args):
    rows, _ = _rows(evidence)
    if rows is None:
        return {"available": False, "reason": "No saved results for selected calculation."}
    sort = args.get("sort_by", "file_order")
    if sort not in ("file_order", "score", "rmsd"):
        return {"error": "Unsupported sort_by."}
    offset = args.get("offset", 0)
    limit = args.get("limit", 15)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_ROWS:
        return {"error": "Invalid offset or limit."}
    key = {"score": "score_kcal_mol", "rmsd": "reference_rmsd_A"}.get(sort)
    if key is not None:
        rows.sort(key=lambda r: (r.get(key, math.inf), r["row_index"]))
    return {"available": True, "total_rows": len(rows), "offset": offset,
            "returned_rows": len(rows[offset:offset + limit]), "sort_by": sort,
            "rows": rows[offset:offset + limit]}


def _structure(evidence):
    checks = evidence.get("checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    raw = checks.get("issues") or []
    issues = []
    for issue in raw:
        if isinstance(issue, dict):
            issues.append(_safe_fields(issue, ("severity", "residue", "problem", "action")))
    issues.sort(key=lambda x: x.get("severity", "").lower() != "error")
    report = evidence.get("report") or {}
    changes = (report.get("repair_changes") or []) + (report.get("preparation_changes") or []) if isinstance(report, dict) else []
    selected = evidence.get("issue")
    return {"available": bool(checks or selected or changes),
            "screening_counts": _safe_fields(checks, ("atom_count", "heavy_atom_count", "residue_count")),
            "issue_count": len(issues), "issues": issues[:MAX_ISSUES],
            "issues_truncated": len(issues) > MAX_ISSUES,
            "selected_issue": _safe_fields(selected, ("severity", "residue", "problem", "action")),
            "recorded_change_count": len(changes),
            "recorded_changes": [_safe_fields(x, ("residue", "atom", "change", "displacement_A"))
                                 for x in changes[:MAX_ISSUES] if isinstance(x, dict)],
            "scope": "Structural screening and recorded changes; not a proof of valid chemistry."}


def _residue(evidence, args):
    residue = args.get("residue")
    if not isinstance(residue, str) or not 1 <= len(residue) <= 60:
        return {"error": "Provide a residue identifier such as A:437:CYS."}
    pdb = evidence.get("pdb")
    if not isinstance(pdb, str) or not pdb:
        return {"available": False, "reason": "No structure loaded in current session."}
    from . import core
    atoms = [a for a in core.atoms(pdb) if core.key(a) == residue]
    if not atoms:
        return {"available": False, "reason": "Residue not found in the selected structure."}
    return {"available": True, "residue": residue, "atom_count": len(atoms),
            "heavy_atom_count": sum(a["element"] not in ("H", "D") for a in atoms),
            "atoms": [{"name": a["name"], "element": a["element"],
                       "alt": a["alt"]} for a in atoms[:100]],
            "atoms_truncated": len(atoms) > 100,
            "scope": "First-model atom inventory, without coordinates or interaction claims."}


def _provenance(evidence):
    config = _load_job_file(evidence, "config.json")
    status = _load_job_file(evidence, "status.json")
    return {"job_available": evidence.get("job") is not None,
            "calculation_settings": _safe_fields(config, (
                "center", "size", "exhaustiveness", "poses", "cpu", "protocol_id", "preparation_id")),
            "seeds": (config.get("seeds") or [])[:30] if isinstance(config, dict) and isinstance(config.get("seeds"), list) else [],
            "job_status": _safe_fields(status, ("state", "completed", "total", "poses_written", "cpu_threads")),
            "preparation_review": _safe_fields(evidence.get("preparation"), (
                "pH_context", "pH", "templates", "repaired_heavy_atoms", "heme_coordination_residue")),
            "structure_source": _safe_fields(evidence.get("source"), ("pdb_id", "format", "source")),
            "scope": "Recorded settings and processing status; missing values have not been inferred."}


def dispatch(name, arguments, evidence):
    """Execute only an allowlisted read-only query, with bounded model arguments."""
    if name not in {spec["function"]["name"] for spec in SCHEMAS}:
        return {"error": "Unknown scientific evidence tool."}
    if not isinstance(arguments, dict):
        return {"error": "Tool arguments must be a JSON object."}
    try:
        if name == "get_calculation_summary":
            return _summary(evidence)
        if name == "get_calculation_rows":
            return _paged(evidence, arguments)
        if name == "get_structure_evidence":
            return _structure(evidence)
        if name == "inspect_residue":
            return _residue(evidence, arguments)
        if name == "get_job_provenance":
            return _provenance(evidence)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"error": "Selected scientific evidence could not be read or parsed."}
    return {"error": "Tool unavailable."}


def from_session(state, job=None):
    """Build private local evidence; raw PDB is never serialized into the LLM prompt."""
    return {"job": Path(job) if job else None,
            "pdb": state.get("selected_pdb") or state.get("pdb"),
            "checks": state.get("assistant_structure_checks"),
            "issue": state.get("assistant_selected_issue"),
            "report": state.get("structure_report"),
            "preparation": state.get("preparation_record"),
            "source": state.get("structure_source")}
