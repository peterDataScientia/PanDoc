from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from . import core


class CuratedHemePreparationError(ValueError):
    pass


def _atom_id(atom):
    chain = atom["chain"] or "_"
    return f"{chain}:{atom['number']}{atom['icode']}"


def _meeko_selector(residue_id: str) -> str:
    parts = str(residue_id).split(":")
    if len(parts) < 2:
        raise CuratedHemePreparationError(f"Invalid residue identifier: {residue_id}")
    chain, number = parts[0], parts[1]
    chain = "" if chain == "_" else chain
    return f"{chain}:{number}"


def _set_assignment(assignments: str, residue: str, template: str) -> str:
    """Add or replace one Meeko residue-template assignment."""
    selector = _meeko_selector(residue)
    tokens = []
    replaced = False
    for raw in (assignments or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        lhs = raw.split("=", 1)[0].strip()
        if lhs == selector:
            if not replaced:
                tokens.append(f"{selector}={template}")
                replaced = True
            continue
        tokens.append(raw)
    if not replaced:
        tokens.append(f"{selector}={template}")
    return ",".join(tokens)


def _validate_thiolate_donor(
    pdb: str,
    *,
    coordination_residue: str,
    protein_donor_atom: str = "SG",
    max_s_h_distance_A: float = 1.55,
):
    """Require the reviewed proximal sulfur to be deprotonated after Meeko prep."""
    atoms = core.atoms(pdb)
    donor = [
        a for a in atoms
        if _atom_id(a) == coordination_residue
        and a["name"].upper() == protein_donor_atom.upper()
    ]
    if len(donor) != 1:
        raise CuratedHemePreparationError(
            f"Expected one donor atom {coordination_residue}:{protein_donor_atom} "
            f"after protein preparation; found {len(donor)}."
        )
    hydrogens = [
        a for a in atoms
        if _atom_id(a) == coordination_residue
        and a["element"].upper() in {"H", "D"}
    ]
    close = [
        a for a in hydrogens
        if math.dist(donor[0]["xyz"], a["xyz"]) <= float(max_s_h_distance_A)
    ]
    if close:
        raise CuratedHemePreparationError(
            f"{coordination_residue}:{protein_donor_atom} is still protonated after "
            "reviewed preparation; the CYP450 proximal cysteine must be thiolate."
        )
    return {
        "coordination_residue": coordination_residue,
        "protein_donor_atom": protein_donor_atom,
        "thiolate_verified": True,
        "sulfur_bound_hydrogen_count": 0,
    }


def _find_heme_atoms(pdb: str, component: str):
    component = str(component or "HEM").upper()
    return [a for a in core.atoms(pdb) if a["res"].upper() == component]


def validate_heme_site(
    pdb: str,
    *,
    heme_component: str = "HEM",
    coordination_residue: str,
    heme_iron_atom: str = "FE",
    protein_donor_atom: str = "SG",
    max_coordination_distance_A: float = 3.0,
):
    atoms = core.atoms(pdb)
    heme_atoms = _find_heme_atoms(pdb, heme_component)
    if not heme_atoms:
        raise CuratedHemePreparationError(
            f"Required heme component {heme_component} was not found."
        )

    heme_residues = sorted({core.key(a) for a in heme_atoms})
    if len(heme_residues) != 1:
        raise CuratedHemePreparationError(
            "Curated heme preparation currently requires exactly one retained "
            f"{heme_component} residue; found {len(heme_residues)}."
        )

    iron = [
        a for a in heme_atoms
        if a["name"].upper() == heme_iron_atom.upper()
        or a["element"].upper() == "FE"
    ]
    if len(iron) != 1:
        raise CuratedHemePreparationError(
            f"Expected one {heme_iron_atom} atom in {heme_residues[0]}; found {len(iron)}."
        )

    donors = [
        a for a in atoms
        if _atom_id(a) == coordination_residue
        and a["name"].upper() == protein_donor_atom.upper()
    ]
    if len(donors) != 1:
        raise CuratedHemePreparationError(
            f"Expected donor atom {coordination_residue}:{protein_donor_atom}; "
            f"found {len(donors)}."
        )

    distance = math.dist(iron[0]["xyz"], donors[0]["xyz"])
    if distance > float(max_coordination_distance_A):
        raise CuratedHemePreparationError(
            f"Heme coordination check failed: {coordination_residue}:{protein_donor_atom} "
            f"to {heme_residues[0]}:{heme_iron_atom} is {distance:.3f} Å, "
            f"above the allowed {float(max_coordination_distance_A):.3f} Å."
        )

    return {
        "heme_residue": heme_residues[0],
        "heme_component": heme_component,
        "iron_atom": heme_iron_atom,
        "coordination_residue": coordination_residue,
        "protein_donor_atom": protein_donor_atom,
        "coordination_distance_A": round(distance, 3),
        "maximum_coordination_distance_A": float(max_coordination_distance_A),
        "heme_atom_count": len(heme_atoms),
    }


def _protein_without_component(pdb: str, component: str) -> str:
    component = str(component).upper()
    kept = []
    for line in pdb.splitlines():
        if line.startswith(("ATOM  ", "HETATM")) and line[17:20].strip().upper() == component:
            continue
        if line.startswith(("CONECT", "LINK  ")):
            continue
        if line.strip() == "END":
            continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\nEND\n"


def _component_only(pdb: str, component: str) -> str:
    component = str(component).upper()
    lines = [
        line for line in pdb.splitlines()
        if line.startswith(("ATOM  ", "HETATM")) and line[17:20].strip().upper() == component
    ]
    if not lines:
        raise CuratedHemePreparationError(f"Component {component} was not found.")
    return "\n".join(lines) + "\nEND\n"


def _merge_prepared_protein_and_heme(prepared_protein: str, heme_pdb: str) -> str:
    protein_lines = [
        line for line in prepared_protein.splitlines()
        if line.startswith(("ATOM  ", "HETATM", "TER"))
    ]
    heme_lines = [
        line for line in heme_pdb.splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
    ]
    return "\n".join(protein_lines + heme_lines) + "\nEND\n"


def _adt_command():
    explicit = os.environ.get("PANDOC_ADT_PREPARE_RECEPTOR4", "").strip()
    if explicit:
        pythonsh = os.environ.get("PANDOC_ADT_PYTHON", "").strip()
        return ([pythonsh, explicit] if pythonsh else [explicit]), "configured-path"

    direct = shutil.which("prepare_receptor4.py")
    if direct:
        return [direct], "path"

    micromamba = shutil.which("micromamba")
    env_name = os.environ.get("PANDOC_ADT_ENV", "pandoc-adt").strip() or "pandoc-adt"
    if micromamba:
        return [micromamba, "run", "-n", env_name, "prepare_receptor4.py"], f"micromamba:{env_name}"

    raise CuratedHemePreparationError(
        "Curated heme preparation requires AutoDockTools prepare_receptor4.py. "
        "The Meeko CCD route is intentionally not used for HEM because Meeko's "
        "HEM automatic template construction is a known unsupported/failing case. "
        "Run this profile on the PanDoc Computer C curated-heme backend."
    )


def _pdbqt_atom_records(path: Path):
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            rows.append({
                "line": line,
                "name": line[12:16].strip(),
                "res": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "number": line[22:26].strip(),
                "xyz": [float(line[30:38]), float(line[38:46]), float(line[46:54])],
                "atype": line.split()[-1] if line.split() else "",
            })
        except Exception:
            continue
    return rows


def _validate_pdbqt_heme(path: Path, before: dict):
    rows = _pdbqt_atom_records(path)
    heme = [r for r in rows if r["res"].upper() == before["heme_component"].upper()]
    iron = [
        r for r in heme
        if r["name"].upper() == before["iron_atom"].upper()
        or r["atype"].upper() == "FE"
    ]
    if len(iron) != 1:
        raise CuratedHemePreparationError(
            "Prepared receptor PDBQT does not contain exactly one retained heme Fe atom."
        )

    chain, number = before["coordination_residue"].split(":", 1)
    donor = [
        r for r in rows
        if (r["chain"] or "_") == chain
        and r["number"] == number
        and r["name"].upper() == before["protein_donor_atom"].upper()
    ]
    if len(donor) != 1:
        raise CuratedHemePreparationError(
            "Prepared receptor PDBQT does not contain the reviewed heme-coordinating donor atom."
        )

    distance = math.dist(iron[0]["xyz"], donor[0]["xyz"])
    if distance > before["maximum_coordination_distance_A"]:
        raise CuratedHemePreparationError(
            f"Prepared receptor lost the reviewed Fe-donor geometry ({distance:.3f} Å)."
        )
    return {
        "heme_atom_count_in_pdbqt": len(heme),
        "iron_autodock_type": iron[0]["atype"],
        "coordination_distance_A": round(distance, 3),
    }


def prepare_curated_heme_receptor(
    pdb: str,
    directory,
    *,
    template_assignments: str = "",
    heme_component: str = "HEM",
    coordination_residue: str,
    heme_iron_atom: str = "FE",
    protein_donor_atom: str = "SG",
    coordination_template: str = "CYX-",
    max_coordination_distance_A: float = 3.0,
    heme_source_pdb: str | None = None,
):
    """Prepare a rigid P450-like heme receptor without Meeko HEM inference.

    The protein is first prepared with PanDoc's reviewed Meeko residue states.
    The crystallographic heme coordinates are then merged back unchanged and the
    complete rigid receptor is converted to PDBQT with AutoDockTools
    prepare_receptor4.py, retaining non-standard residues. This follows the
    established AutoDock/Vina receptor-preparation route used for heme proteins
    while avoiding Meeko's known HEM CCD-template failure.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # Repair/protonation may operate on a rebuilt protein, but the heme itself
    # must come from the crystallographic source rather than from PDBFixer output.
    heme_pdb = _component_only(heme_source_pdb or pdb, heme_component)
    protein_only = _protein_without_component(pdb, heme_component)
    preconversion_complex = _merge_prepared_protein_and_heme(protein_only, heme_pdb)
    before = validate_heme_site(
        preconversion_complex,
        heme_component=heme_component,
        coordination_residue=coordination_residue,
        heme_iron_atom=heme_iron_atom,
        protein_donor_atom=protein_donor_atom,
        max_coordination_distance_A=max_coordination_distance_A,
    )
    (directory / "heme_site_before_preparation.json").write_text(
        json.dumps(before, indent=2)
    )
    (directory / "heme_crystallographic.pdb").write_text(heme_pdb)
    (directory / "protein_only_for_reviewed_prep.pdb").write_text(protein_only)

    # Force the proximal P450 cysteine into Meeko's concrete thiolate template.
    # This prevents a neutral SG-H cysteine from being merged next to the heme Fe.
    reviewed_assignments = _set_assignment(
        template_assignments,
        coordination_residue,
        coordination_template,
    )
    (directory / "protein_meeko_template_assignments.txt").write_text(
        reviewed_assignments + "\n"
    )

    # Meeko is used only for standard protein residue chemistry/protonation.
    protein_pdbqt = core.prepare_receptor(
        protein_only,
        directory / "protein_meeko",
        template_assignments=reviewed_assignments,
    )
    del protein_pdbqt
    prepared_protein_path = directory / "protein_meeko" / "receptor_prepared.pdb"
    if not prepared_protein_path.is_file():
        raise CuratedHemePreparationError(
            "Reviewed protein-only preparation did not produce receptor_prepared.pdb."
        )

    prepared_protein = prepared_protein_path.read_text()
    thiolate_audit = _validate_thiolate_donor(
        prepared_protein,
        coordination_residue=coordination_residue,
        protein_donor_atom=protein_donor_atom,
    )
    (directory / "proximal_cysteine_thiolate.json").write_text(
        json.dumps(thiolate_audit, indent=2)
    )

    merged = _merge_prepared_protein_and_heme(
        prepared_protein,
        heme_pdb,
    )
    merged_path = directory / "protein_reviewed_plus_heme.pdb"
    merged_path.write_text(merged)

    # Verify the merge before conversion.
    merge_audit = validate_heme_site(
        merged,
        heme_component=heme_component,
        coordination_residue=coordination_residue,
        heme_iron_atom=heme_iron_atom,
        protein_donor_atom=protein_donor_atom,
        max_coordination_distance_A=max_coordination_distance_A,
    )
    (directory / "heme_site_after_merge.json").write_text(json.dumps(merge_audit, indent=2))

    prefix, backend = _adt_command()
    receptor_pdbqt = directory / "receptor_curated_heme.pdbqt"
    cmd = prefix + [
        "-r", str(merged_path),
        "-o", str(receptor_pdbqt),
        "-U", "nphs_lps_waters",
        "-v",
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (directory / "autodocktools_command.json").write_text(json.dumps({
        "backend": backend,
        "command": cmd,
        "returncode": completed.returncode,
    }, indent=2))
    (directory / "autodocktools_preparation.log").write_text(
        completed.stdout + "\n" + completed.stderr
    )

    if completed.returncode != 0 or not receptor_pdbqt.is_file() or receptor_pdbqt.stat().st_size == 0:
        raise CuratedHemePreparationError(
            "AutoDockTools curated heme receptor conversion failed. "
            f"Review {directory / 'autodocktools_preparation.log'}."
        )

    pdbqt_audit = _validate_pdbqt_heme(receptor_pdbqt, before)

    audit = {
        "mode": "curated_heme_autodocktools",
        "status": "prepared_curated_heme",
        "protein_preparation": "Meeko reviewed standard-residue states",
        "proximal_cysteine_template": coordination_template,
        "proximal_cysteine_thiolate": thiolate_audit,
        "heme_preparation": (
            "crystallographic HEM heavy-atom coordinates retained; "
            "AutoDockTools receptor conversion"
        ),
        "heme_source": "crystallographic source prior to PDBFixer heme handling",
        "heme_propionate_context": (
            "No H2A/H2D propionate hydrogens are added by PanDoc; "
            "3S79 is prepared at pH 7.4."
        ),
        "autodocktools_cleanup": "nphs_lps_waters (nonstdres intentionally omitted to retain HEM)",
        "autodocktools_backend": backend,
        "before": before,
        "after_protein_heme_merge": merge_audit,
        "pdbqt": pdbqt_audit,
        "remote_residue_exclusion": False,
        "ccd_template_generation": False,
        "validation_required": True,
        "validation_note": (
            "Successful preparation does not establish docking validity. "
            "Redock the crystallographic substrate and evaluate fixed-frame "
            "heavy-atom RMSD before production docking."
        ),
    }
    (directory / "curated_heme_audit.json").write_text(json.dumps(audit, indent=2))
    return receptor_pdbqt, audit
