"""Risk-proportionate preparation readiness, separate from docking validation.

A valid PDBQT with documented remote coordinate limitations is allowed to
proceed to redocking. Chemical/integrity failures are not reclassified as
warnings. Preparation readiness never implies an acceptable redocking RMSD.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def _validate_pdbqt(path):
    """Read a written receptor PDBQT, catching incomplete/nonfinite records."""
    path = Path(path) if path else None
    if not path or not path.is_file() or not path.stat().st_size:
        return "Prepared receptor PDBQT is missing or empty."
    count = 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        count += 1
        try:
            coords = [float(line[start:start + 8]) for start in (30, 38, 46)]
            charge = float(line[70:76])
            atom_type = line[77:].strip()
        except (ValueError, IndexError):
            return f"Unparseable PDBQT atom record {count}."
        if not all(math.isfinite(value) for value in coords + [charge]):
            return f"Nonfinite PDBQT coordinates or charge at atom {count}."
        if not atom_type:
            return f"Missing PDBQT atom type at atom {count}."
    if count == 0:
        return "Prepared PDBQT contains no receptor atoms."
    return None


def assess(*, target, preparation_mode, receptor_pdbqt, structure_issues=(),
           remote_exclusions=(), heme_audit=None, minimum_exclusion_distance_A=20.0):
    """Return blockers, warnings and explicit *pre-redocking* readiness.

    Structural screening 'Error' items are blocking; 'Warning' items do not
    block. Removal of a distant residue must have a recorded distance and a
    documented minimum. Coordinated HEM requires an independent HEM audit;
    uncertain iron scoring parameters remain a disclosed limitation.
    """
    blockers = []
    warnings = []
    for issue in structure_issues or ():
        entry = {
            "source": "coordinate_screening",
            "residue": issue.get("residue", "Structure"),
            "message": issue.get("problem", "Unspecified coordinate issue"),
            "recommended_action": issue.get("action", ""),
        }
        if str(issue.get("severity", "")).lower() == "error":
            blockers.append(entry)
        else:
            warnings.append(entry)
    for removal in remote_exclusions or ():
        name = str(removal.get("residue", "?"))
        distance = removal.get("distance_to_reference_A")
        minimum = float(removal.get("minimum_allowed_distance_A", minimum_exclusion_distance_A))
        entry = {
            "source": "remote_residue_exclusion",
            "residue": name,
            "distance_to_reference_A": distance,
            "message": (
                f"Residue {name} excluded after Meeko failure; nearest distance "
                f"to reference ligand {distance} Å. Original receptor was modified."
            ),
            "recommended_action": (
                "Keep the exclusion in the methods and compare redocking results "
                "with a corrected terminal-residue model if needed."
            ),
        }
        if not isinstance(distance, (float, int)) or not math.isfinite(distance) or distance < minimum:
            blockers.append(entry)
        else:
            warnings.append(entry)
    failure = _validate_pdbqt(receptor_pdbqt)
    if failure:
        blockers.append({
            "source": "prepared_output",
            "residue": "Receptor",
            "message": failure,
            "recommended_action": "Correct preparation and regenerate PDBQT.",
        })
    if preparation_mode == "curated_heme":
        heme = (heme_audit or {}).get("pdbqt") or {}
        if not heme.get("heme_atom_count_in_pdbqt") or not heme.get("all_receptor_heavy_atom_types_verified"):
            blockers.append({
                "source": "heme_integrity",
                "residue": "HEM",
                "message": "Curated Fe/HEM heavy-atom preservation audit is missing or incomplete.",
                "recommended_action": "Run the validated curated AutoDockTools preparation.",
            })
        if heme.get("iron_parameterization_review_required") is True:
            warnings.append({
                "source": "iron_scoring",
                "residue": "HEM:FE",
                "message": (
                    "AutoDockTools did not assign Fe Gasteiger parameters. Fe charges and "
                    "Vina scoring suitability require scientific review."
                ),
                "recommended_action": "Review metal parameterization before production docking.",
            })
    if blockers:
        readiness = "blocked"
    elif warnings:
        readiness = "ready_with_warnings"
    else:
        readiness = "ready_for_redocking"
    return {
        "target": target,
        "readiness": readiness,
        "ready_for_redocking": not bool(blockers),
        "production_validated": False,
        "blocking_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "next_step": (
            "Correct preparation errors and rerun QC."
            if blockers else
            "Run crystallographic-ligand redocking; inspect RMSD, poses and scoring."
        ),
        "scope": (
            "Preparation-only gate. A passing receptor is not publication-validated. "
            "Redocking, binding-site and (where relevant) metal scoring review remain necessary."
        ),
    }


def manifest(requested_targets, results, errors, pending_targets=()):
    """Explicitly account for every requested target in even a partial ZIP."""
    requested = list(dict.fromkeys(requested_targets))
    completed = list(dict.fromkeys(
        str(record["target"]) for record in results if record.get("target") in requested
    ))
    failed = list(dict.fromkeys(
        str(record["target"]) for record in errors if record.get("target") in requested
    ))
    pending = [name for name in requested if name not in completed and name not in failed]
    failed = [name for name in failed if name not in completed]
    state = "complete" if not pending else "partial"
    return {
        "state": state,
        "complete": not bool(pending),
        "requested_targets": requested,
        "completed_targets": completed,
        "failed_targets": failed,
        "pending_targets": pending,
        "message": (
            "All requested targets have finished processing (some may have failed)."
            if not pending else
            "Partial bundle: pending receptor profiles have not finished or were not included."
        ),
        "quality_interpretation": (
            "Completed means preparation finished, not successful redocking "
            "or suitability for production docking."
        ),
    }


def write_manifest(directory, requested_targets, results, errors, pending_targets=()):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record = manifest(requested_targets, results, errors, pending_targets)
    (directory / "PROFILE_MANIFEST.json").write_text(json.dumps(record, indent=2))
    return record
