from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from . import core, pdb_search, phprep, structure_checks


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


def _validate_overrides(rows, overrides):
    by_residue = {r["residue"]: r for r in rows}
    missing = sorted(set(overrides) - set(by_residue))
    if missing:
        raise ValueError(
            "Reviewed profile contains residues absent from the PROPKA table: "
            + ", ".join(missing)
        )
    return by_residue


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

    repaired = core.repair_heavy_atoms(selected)
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

    overrides = dict(cfg.get("overrides", {}))
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

    if mode == "curated_heme" and not force_curated:
        status = "held_for_curated_heme_preparation"
        (root / "CURATED_HEME_HOLD.txt").write_text(
            "Generic Meeko preparation intentionally blocked.\n"
            "Preserve HEM/Fe-Cys coordination and use a curated heme-aware preparation workflow.\n"
        )
    else:
        receptor_pdbqt = core.prepare_receptor(
            repaired,
            root / "prepared_receptor",
            template_assignments=assignments,
        )
        status = "prepared"

    result = {
        "profile_set": meta.get("profile_set"),
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
