from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

import pandas as pd

from . import core, pdb_search, phprep, structure_checks, heme_prep


PROFILE_PATH = Path(__file__).with_name("receptor_profiles.json")


def load_profiles():
    data = json.loads(PROFILE_PATH.read_text())
    return data["targets"], data


def available_profiles():
    targets, meta = load_profiles()
    return {
        "profile_set": meta.get("profile_set"),
        "schema_version": meta.get("schema_version"),
        "targets": sorted(targets),
    }


def _write_csv(rows, path):
    pd.DataFrame(list(rows or [])).to_csv(path, index=False)


def _component_hits(rows, residue_name, chain=None):
    exact = [
        r["residue"] for r in rows
        if r["name"] == residue_name and (chain is None or r["chain"] == chain)
    ]
    if exact:
        return exact
    return [r["residue"] for r in rows if r["name"] == residue_name]


def _select_profile_receptor(pdb, inventory_rows, cfg):
    protein = [
        r["residue"] for r in inventory_rows
        if r["kind"] == "Protein" and r["chain"] == cfg["chain"]
    ]
    if not protein:
        raise ValueError(f"No protein residues found for chain {cfg['chain']}.")

    retained = []
    for component in cfg.get("retain_components", []):
        hits = _component_hits(inventory_rows, component, cfg["chain"])
        if not hits:
            raise ValueError(f"Required retained component {component} was not found.")
        retained.extend(hits)

    for water in cfg.get("retain_waters_primary", []):
        if water not in {r["residue"] for r in inventory_rows}:
            raise ValueError(f"Required retained water {water} was not found.")
        retained.append(water)

    return core.select(pdb, protein + retained), retained


def _normalize_override_keys(overrides):
    """Accept both legacy chain:number:resname and canonical chain:number keys."""
    normalized = {}
    for key, state in (overrides or {}).items():
        parts = str(key).split(":")
        canonical = ":".join(parts[:2]) if len(parts) >= 2 else str(key)
        if canonical in normalized and normalized[canonical] != state:
            raise ValueError(f"Conflicting reviewed states for {canonical}.")
        normalized[canonical] = state
    return normalized


def _validate_overrides(rows, overrides):
    by_residue = {r["residue"]: r for r in rows}
    missing = sorted(set(overrides) - set(by_residue))
    if missing:
        raise ValueError(
            "Reviewed profile contains residues absent from the PROPKA table: "
            + ", ".join(missing)
        )
    return by_residue


def _residue_prefix(atom):
    chain = atom["chain"] or "_"
    return f"{chain}:{atom['number']}{atom['icode']}"


def _distance_to_reference(pdb, residue_prefix, reference_pdb):
    residue_atoms = [
        a for a in core.atoms(pdb)
        if _residue_prefix(a) == residue_prefix and a["element"] not in ("H", "D")
    ]
    reference_atoms = [
        a for a in core.atoms(reference_pdb)
        if a["element"] not in ("H", "D")
    ]
    if not residue_atoms or not reference_atoms:
        return None
    return min(
        math.dist(a["xyz"], b["xyz"])
        for a in residue_atoms
        for b in reference_atoms
    )


def _strip_hydrogens(pdb):
    """Remove deposited hydrogens before heavy-atom rebuilding.

    Incomplete residues can carry hydrogens that were consistent with the
    truncated experimental model but become inconsistent after PDBFixer adds
    missing heavy atoms. Meeko will regenerate hydrogens from reviewed residue
    templates, so receptor reconstruction should start from heavy atoms only.
    """
    kept = []
    for line in pdb.splitlines():
        if line.startswith(("ATOM  ", "HETATM")):
            name = line[12:16].strip()
            element = line[76:78].strip().upper() if len(line) >= 78 else ""
            if not element:
                letters = "".join(ch for ch in name if ch.isalpha())
                element = letters[:1].upper()
            if element in {"H", "D"}:
                continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def _remove_residue_prefixes(pdb, prefixes):
    prefixes = set(prefixes)
    kept = []
    for line in pdb.splitlines():
        if line.startswith(("ATOM  ", "HETATM")):
            chain = line[21:22].strip() or "_"
            number = line[22:26].strip()
            icode = line[26:27].strip()
            prefix = f"{chain}:{number}{icode}"
            if prefix in prefixes:
                continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def _filter_assignments(assignments, excluded_prefixes):
    excluded = set(excluded_prefixes)
    kept = []
    for item in (assignments or "").split(","):
        item = item.strip()
        if not item:
            continue
        residue = item.split("=", 1)[0].strip()
        if residue not in excluded:
            kept.append(item)
    return ",".join(kept)


def _prepare_with_remote_fallback(repaired, reference_pdb, directory, assignments,
                                  minimum_distance_A=20.0, max_exclusions=5):
    """Prepare normally; if Meeko rejects remote residues, remove only those
    residues, remove their template assignments too, audit every exclusion, and retry.
    """
    current_pdb = repaired
    exclusions = []
    excluded_prefixes = set()

    for attempt in range(max_exclusions + 1):
        current_assignments = _filter_assignments(assignments, excluded_prefixes)
        current_dir = (
            Path(directory)
            if attempt == 0
            else Path(directory).parent / f"{Path(directory).name}_remote_fallback_{attempt}"
        )
        try:
            path = core.prepare_receptor(
                current_pdb,
                current_dir,
                template_assignments=current_assignments,
            )
            return path, exclusions, current_pdb
        except ValueError as exc:
            bad = sorted(set(re.findall(r"[A-Za-z0-9_]+:[0-9]+[A-Za-z]?", str(exc))))
            bad = [r for r in bad if r not in excluded_prefixes]
            if not bad:
                raise

            newly_excluded = []
            for residue in bad:
                distance = _distance_to_reference(current_pdb, residue, reference_pdb)
                if distance is None or distance < float(minimum_distance_A):
                    raise ValueError(
                        f"Meeko rejected {residue}, which cannot be safely excluded "
                        f"(distance to reference ligand: {distance})."
                    ) from exc
                newly_excluded.append(residue)
                exclusions.append({
                    "residue": residue,
                    "distance_to_reference_A": round(distance, 3),
                    "reason": "Meeko template/bonding failure; remote from docking site",
                    "minimum_allowed_distance_A": float(minimum_distance_A),
                    "retry_number": attempt + 1,
                })

            if not newly_excluded:
                raise
            excluded_prefixes.update(newly_excluded)
            current_pdb = _remove_residue_prefixes(current_pdb, newly_excluded)

    raise ValueError(
        f"Meeko preparation still failed after {max_exclusions} audited remote-residue exclusions."
    )

def prepare_from_profile(target, output_dir, force_curated=False):
    targets, meta = load_profiles()
    if target not in targets:
        raise KeyError(f"Unknown receptor profile: {target}")
    cfg = targets[target]

    root = Path(output_dir)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    cif_text, source = pdb_search.download(cfg["pdb"])
    pdb = core.normalize_structure(cif_text, ".cif")
    (root / "00_source_normalized.pdb").write_text(pdb)

    inventory = structure_checks.inventory(pdb)
    _write_csv(inventory["components"], root / "01_components.csv")
    _write_csv(inventory["alternates"], root / "02_alternate_conformations.csv")
    (root / "03_connections.txt").write_text(
        "\n".join(inventory["connections"]) or "No LINK/SSBOND records reported.\n"
    )

    reference_hits = _component_hits(
        inventory["components"], cfg["reference_component"], cfg["chain"]
    )
    if not reference_hits:
        raise ValueError(
            f"Reference component {cfg['reference_component']} was not found."
        )
    reference_residue = reference_hits[0]
    reference_pdb = core.select(pdb, [reference_residue])
    (root / "04_reference_ligand.pdb").write_text(reference_pdb)

    selected, retained = _select_profile_receptor(
        pdb, inventory["components"], cfg
    )
    (root / "05_receptor_selected.pdb").write_text(selected)

    checks_before = structure_checks.check(selected)
    _write_csv(checks_before["issues"], root / "06_issues_before_repair.csv")

    heavy_only = _strip_hydrogens(selected)
    (root / "06b_receptor_heavy_only_before_repair.pdb").write_text(heavy_only)

    repaired = core.repair_heavy_atoms(heavy_only)
    (root / "07_receptor_repaired.pdb").write_text(repaired)

    checks_after = structure_checks.check(repaired)
    _write_csv(checks_after["issues"], root / "08_issues_after_repair.csv")
    _write_csv(
        structure_checks.changes(selected, repaired),
        root / "09_repair_changes.csv",
    )

    ph_rows, _ = phprep.predict_protein_states(
        repaired, root / "propka", cfg["pH"]
    )
    _write_csv(ph_rows, root / "10_propka.csv")

    overrides = _normalize_override_keys(cfg.get("overrides", {}))
    _validate_overrides(ph_rows, overrides)
    assignments = phprep.template_assignments(ph_rows, overrides)
    (root / "11_meeko_template_assignments.txt").write_text(assignments + "\n")

    reviewed_rows = []
    for row in ph_rows:
        item = dict(row)
        item["reviewed_state"] = overrides.get(row["residue"], row.get("template"))
        item["overridden"] = row["residue"] in overrides
        reviewed_rows.append(item)
    _write_csv(reviewed_rows, root / "12_reviewed_protonation_states.csv")

    mode = cfg.get("preparation_mode", "standard")
    status = "reviewed"
    receptor_pdbqt = None
    remote_exclusions = []

    heme_audit = None
    if mode == "curated_heme":
        heme_cfg = cfg.get("heme", {})
        receptor_pdbqt, heme_audit = heme_prep.prepare_curated_heme_receptor(
            repaired,
            root / "prepared_receptor_heme",
            template_assignments=assignments,
            heme_component=heme_cfg.get("component", "HEM"),
            coordination_residue=heme_cfg["coordination_residue"],
            heme_iron_atom=heme_cfg.get("iron_atom", "FE"),
            protein_donor_atom=heme_cfg.get("protein_donor_atom", "SG"),
            max_coordination_distance_A=float(
                heme_cfg.get("max_coordination_distance_A", 3.0)
            ),
        )
        status = "prepared_curated_heme"
    else:
        receptor_pdbqt, remote_exclusions, prepared_input = _prepare_with_remote_fallback(
            repaired,
            reference_pdb,
            root / "prepared_receptor",
            assignments,
            minimum_distance_A=20.0,
        )
        if remote_exclusions:
            _write_csv(remote_exclusions, root / "13_remote_meeko_exclusions.csv")
            (root / "14_receptor_after_remote_exclusions.pdb").write_text(prepared_input)
            status = "prepared_with_remote_exclusion"
        else:
            status = "prepared"

    result = {
        "profile_set": meta.get("profile_set"),
        "engine_revision": "curated-heme-v4",
        "target": target,
        "pdb": cfg["pdb"],
        "chain": cfg["chain"],
        "reference_component": cfg["reference_component"],
        "reference_residue": reference_residue,
        "pH": cfg["pH"],
        "preparation_mode": mode,
        "status": status,
        "retained_residues": retained,
        "validation_water_candidates": cfg.get("validation_water_candidates", []),
        "override_count": len(overrides),
        "issues_after_repair": len(checks_after["issues"]),
        "remote_meeko_exclusions": remote_exclusions,
        "heme_audit": heme_audit,
        "receptor_pdbqt": str(receptor_pdbqt) if receptor_pdbqt else None,
        "source_url": source.get("url"),
        "notes": cfg.get("notes", []),
    }
    (root / "13_profile_result.json").write_text(json.dumps(result, indent=2))
    return result


def prepare_many(targets, output_root, force_curated=False):
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    for target in targets:
        try:
            results.append(
                prepare_from_profile(
                    target,
                    out / target,
                    force_curated=force_curated,
                )
            )
        except Exception as exc:
            errors.append({"target": target, "error": str(exc)})
    _write_csv(results, out / "ALL_PROFILE_RESULTS.csv")
    _write_csv(errors, out / "ERRORS.csv")
    return {
        "results": results,
        "errors": errors,
        "bundle": core.bundle(out),
    }
