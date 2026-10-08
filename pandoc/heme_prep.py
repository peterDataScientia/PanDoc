from __future__ import annotations

import json
import math
from pathlib import Path

from . import core


class CuratedHemePreparationError(ValueError):
    pass


def _atom_id(atom):
    chain = atom["chain"] or "_"
    return f"{chain}:{atom['number']}{atom['icode']}"


def _find_heme_atoms(pdb: str, component: str):
    component = str(component or "HEM").upper()
    return [
        atom for atom in core.atoms(pdb)
        if atom["res"].upper() == component
    ]


def validate_heme_site(
    pdb: str,
    *,
    heme_component: str = "HEM",
    coordination_residue: str,
    heme_iron_atom: str = "FE",
    protein_donor_atom: str = "SG",
    max_coordination_distance_A: float = 3.0,
):
    """Validate the rigid P450 heme site before receptor conversion.

    This function does not invent metal parameters. It verifies that the
    crystallographic heme and the expected protein donor are both present and
    that their deposited/repaired geometry remains compatible with the reviewed
    coordination assignment.
    """
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


def _pdbqt_has_iron(path: Path):
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        atom_name = line[12:16].strip().upper() if len(line) >= 16 else ""
        autodock_type = line.split()[-1].upper() if line.split() else ""
        if atom_name == "FE" or autodock_type == "FE":
            return True
    return False


def prepare_curated_heme_receptor(
    pdb: str,
    directory,
    *,
    template_assignments: str = "",
    heme_component: str = "HEM",
    coordination_residue: str,
    heme_iron_atom: str = "FE",
    protein_donor_atom: str = "SG",
    max_coordination_distance_A: float = 3.0,
):
    """Prepare a rigid heme-containing receptor with strict structural gates.

    Meeko is allowed to convert the retained, chemically connected receptor,
    but PanDoc never removes the heme or the coordinating residue to make the
    conversion pass. Successful file generation is followed by explicit checks
    that the Fe atom remains in the receptor output. Docking validation remains
    mandatory before the receptor is accepted for production docking.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    before = validate_heme_site(
        pdb,
        heme_component=heme_component,
        coordination_residue=coordination_residue,
        heme_iron_atom=heme_iron_atom,
        protein_donor_atom=protein_donor_atom,
        max_coordination_distance_A=max_coordination_distance_A,
    )
    (directory / "heme_site_before_preparation.json").write_text(
        json.dumps(before, indent=2)
    )

    try:
        receptor_pdbqt = core.prepare_receptor(
            pdb,
            directory / "meeko",
            template_assignments=template_assignments,
        )
    except ValueError as exc:
        raise CuratedHemePreparationError(
            "Curated heme receptor conversion failed. PanDoc did not delete HEM, "
            "the coordinating cysteine, or any other residue to force success. "
            f"Review the Meeko diagnostics: {exc}"
        ) from exc

    prepared_pdb = directory / "meeko" / "receptor_prepared.pdb"
    if not prepared_pdb.is_file():
        raise CuratedHemePreparationError(
            "Meeko did not write receptor_prepared.pdb for curated heme verification."
        )

    after = validate_heme_site(
        prepared_pdb.read_text(),
        heme_component=heme_component,
        coordination_residue=coordination_residue,
        heme_iron_atom=heme_iron_atom,
        protein_donor_atom=protein_donor_atom,
        max_coordination_distance_A=max_coordination_distance_A,
    )
    if not _pdbqt_has_iron(Path(receptor_pdbqt)):
        raise CuratedHemePreparationError(
            "The generated receptor PDBQT does not contain an Fe atom. "
            "The curated heme receptor is not valid for docking."
        )

    audit = {
        "mode": "curated_heme",
        "status": "prepared_curated_heme",
        "before": before,
        "after": after,
        "heme_retained_in_pdbqt": True,
        "remote_residue_exclusion": False,
        "validation_required": True,
        "validation_note": (
            "Successful receptor conversion does not establish docking validity. "
            "Redock the crystallographic ligand and evaluate fixed-frame heavy-atom RMSD "
            "before production docking."
        ),
    }
    (directory / "curated_heme_audit.json").write_text(json.dumps(audit, indent=2))
    return Path(receptor_pdbqt), audit
