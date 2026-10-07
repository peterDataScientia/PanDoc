from __future__ import annotations

import math
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from . import core, pdb_search, phprep, structure_checks


TARGETS = {
    "FYN": {
        "pdb": "10DJ",
        "chain": "A",
        "reference_component": "H8H",
        "pH": 7.4,
        "retain_components": [],
    },
    "AR": {
        "pdb": "2AMA",
        "chain": "A",
        "reference_component": "DHT",
        "pH": 7.4,
        "retain_components": [],
    },
    "CYP19A1": {
        "pdb": "3S79",
        "chain": "A",
        "reference_component": "ASD",
        "pH": 7.4,
        "retain_components": ["HEM"],
    },
    "PGR": {
        "pdb": "1A28",
        "chain": "A",
        "reference_component": "STR",
        "pH": 7.4,
        "retain_components": [],
    },
}


def _write_csv(rows, path):
    pd.DataFrame(list(rows or [])).to_csv(path, index=False)


def _pick_components(rows, name, chain=None):
    exact = [r["residue"] for r in rows if r["name"] == name and (chain is None or r["chain"] == chain)]
    if exact:
        return exact
    return [r["residue"] for r in rows if r["name"] == name]


def _binding_site_waters(pdb, reference_residues, cutoff=4.0):
    atoms = core.atoms(pdb)
    ref = [
        a for a in atoms
        if core.key(a) in set(reference_residues) and a["element"] not in ("H", "D")
    ]
    if not ref:
        return []
    waters = {}
    for atom in atoms:
        if atom["res"] not in core.WATERS or atom["element"] in ("H", "D"):
            continue
        waters.setdefault(core.key(atom), []).append(atom)
    out = []
    for residue, wat_atoms in waters.items():
        d = min(math.dist(a["xyz"], b["xyz"]) for a in wat_atoms for b in ref)
        if d <= float(cutoff):
            out.append({
                "water_residue": residue,
                "min_distance_to_reference_A": round(d, 3),
                "cutoff_A": float(cutoff),
                "status": "candidate structural water; review before retaining",
            })
    return sorted(out, key=lambda r: r["min_distance_to_reference_A"])


def _default_review_state(row):
    if row["residue_name"] == "HIS" and str(row["suggested_state"]).startswith("neutral"):
        return "REVIEW HID/HIE"
    return row.get("template") or row.get("suggested_state")


def _run_one(base_dir, name, cfg):
    tdir = base_dir / f"{name}_{cfg['pdb']}"
    tdir.mkdir(parents=True, exist_ok=True)

    cif_text, source = pdb_search.download(cfg["pdb"])
    pdb = core.normalize_structure(cif_text, ".cif")
    (tdir / "source_normalized.pdb").write_text(pdb)

    inventory = structure_checks.inventory(pdb)
    rows = inventory["components"]
    _write_csv(rows, tdir / "01_components.csv")
    _write_csv(inventory["alternates"], tdir / "02_alternate_conformations.csv")
    (tdir / "03_connections.txt").write_text(
        "\n".join(inventory["connections"]) or "No LINK/SSBOND records reported.\n"
    )

    protein_residues = [
        r["residue"] for r in rows
        if r["kind"] == "Protein" and r["chain"] == cfg["chain"]
    ]
    if not protein_residues:
        raise ValueError(f"No protein residues found for chain {cfg['chain']}.")

    reference_residues = _pick_components(
        rows, cfg["reference_component"], cfg["chain"]
    )
    if not reference_residues:
        raise ValueError(
            f"Reference component {cfg['reference_component']} was not found."
        )
    reference_residue = reference_residues[0]
    reference = core.select(pdb, [reference_residue])
    (tdir / "reference_ligand.pdb").write_text(reference)

    retained = []
    for component in cfg["retain_components"]:
        retained.extend(_pick_components(rows, component, cfg["chain"]))

    selected = core.select(pdb, protein_residues + retained)
    (tdir / "selected_receptor_before_repair.pdb").write_text(selected)

    before = structure_checks.check(selected)
    _write_csv(before["issues"], tdir / "04_structure_issues_before_repair.csv")

    repaired = core.repair_heavy_atoms(selected)
    (tdir / "selected_receptor_repaired.pdb").write_text(repaired)

    after = structure_checks.check(repaired)
    _write_csv(after["issues"], tdir / "05_structure_issues_after_repair.csv")
    _write_csv(
        structure_checks.changes(selected, repaired),
        tdir / "06_repair_changes.csv",
    )

    propka_dir = tdir / "propka"
    ph_rows, _ = phprep.predict_protein_states(repaired, propka_dir, cfg["pH"])
    reviewed = []
    for row in ph_rows:
        item = dict(row)
        item["batch_default"] = _default_review_state(row)
        reviewed.append(item)
    _write_csv(reviewed, tdir / "07_protonation_review.csv")
    _write_csv(
        [r for r in reviewed if r.get("near_pKa") or r.get("review_required")],
        tdir / "08_NEEDS_REVIEW.csv",
    )
    _write_csv(
        [r for r in reviewed if r["residue_name"] == "HIS"],
        tdir / "09_histidines.csv",
    )

    waters = _binding_site_waters(pdb, [reference_residue], cutoff=4.0)
    _write_csv(waters, tdir / "10_binding_site_waters.csv")

    summary = {
        "target": name,
        "pdb": cfg["pdb"],
        "chain": cfg["chain"],
        "reference_component": cfg["reference_component"],
        "reference_residue": reference_residue,
        "pH": cfg["pH"],
        "retained_components": ";".join(cfg["retain_components"]) or "none",
        "retained_residues": ";".join(retained) or "none",
        "heavy_atoms_before_repair": before["heavy_atom_count"],
        "heavy_atoms_after_repair": after["heavy_atom_count"],
        "issues_before_repair": len(before["issues"]),
        "issues_after_repair": len(after["issues"]),
        "protonation_rows": len(reviewed),
        "needs_review": sum(1 for r in reviewed if r.get("near_pKa") or r.get("review_required")),
        "histidines": sum(1 for r in reviewed if r["residue_name"] == "HIS"),
        "candidate_waters_within_4A": len(waters),
        "source_url": source.get("url"),
    }
    _write_csv([summary], tdir / "00_summary.csv")
    return summary


def run_batch(base_dir, max_workers=2):
    base_dir = Path(base_dir)
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True)

    summaries = []
    errors = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_one, base_dir, name, cfg): (name, cfg)
            for name, cfg in TARGETS.items()
        }
        for future in as_completed(futures):
            name, cfg = futures[future]
            try:
                summaries.append(future.result())
            except Exception as exc:
                errors.append({
                    "target": name,
                    "pdb": cfg["pdb"],
                    "error": str(exc),
                })

    summaries = sorted(summaries, key=lambda r: list(TARGETS).index(r["target"]))
    _write_csv(summaries, base_dir / "ALL_TARGETS_SUMMARY.csv")
    _write_csv(errors, base_dir / "ERRORS.csv")

    bundle = core.bundle(base_dir)
    return {
        "summary": summaries,
        "errors": errors,
        "bundle": bundle,
    }
