from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

from . import core


class CuratedHemePreparationError(ValueError):
    pass


PACKAGED_HEM_TEMPLATE = Path(__file__).with_name("templates") / "HEM_meeko_template.json"
HEME_TEMPLATE_KEY = "HEM_PANDOC"
DEFAULT_CYP450_THIOLATE_TEMPLATE = "CYX-"


def _atom_id(atom):
    chain = atom["chain"] or "_"
    return f"{chain}:{atom['number']}{atom['icode']}"


def _meeko_selector(residue_id: str):
    """Convert PanDoc chain:number[:resname] IDs to Meeko chain:number selectors."""
    parts = str(residue_id).split(":")
    if len(parts) < 2:
        raise CuratedHemePreparationError(f"Invalid residue identifier: {residue_id}")
    chain, number = parts[0], parts[1]
    chain = "" if chain == "_" else chain
    return f"{chain}:{number}"


def _set_assignment(assignments: str, residue: str, template: str):
    """Add or replace one Meeko --set_template assignment deterministically."""
    residue = _meeko_selector(residue)
    tokens = []
    replaced = False
    for raw in (assignments or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        lhs = raw.split("=", 1)[0].strip()
        if lhs == residue:
            if not replaced:
                tokens.append(f"{residue}={template}")
                replaced = True
            continue
        tokens.append(raw)
    if not replaced:
        tokens.append(f"{residue}={template}")
    return ",".join(tokens)


def _copy_packaged_heme_template(component: str, output_json: Path):
    """Copy the reviewed static HEM template into the experiment for provenance.

    Runtime CCD template generation is intentionally not used for HEM because
    upstream Meeko documents Fe-containing cofactor/CCD failures. The packaged
    template is versioned with PanDoc and can therefore be audited exactly.
    """
    component = str(component or "").strip().upper()
    if component != "HEM":
        raise CuratedHemePreparationError(
            f"The curated static template currently supports HEM, not {component or 'an empty component'}."
        )
    if not PACKAGED_HEM_TEMPLATE.is_file():
        raise CuratedHemePreparationError(
            f"Packaged HEM template is missing: {PACKAGED_HEM_TEMPLATE}"
        )
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PACKAGED_HEM_TEMPLATE, output_json)
    digest = hashlib.sha256(output_json.read_bytes()).hexdigest()
    return output_json, digest


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
    """Validate the rigid P450 heme site before/after receptor conversion."""
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
        "heme_heavy_atom_names": sorted(
            a["name"] for a in heme_atoms if a["element"] not in ("H", "D")
        ),
    }


def _link_endpoint(line: str, second: bool = False):
    if second:
        name = line[42:46].strip()
        res = line[47:50].strip()
        chain = line[51:52].strip() or "_"
        number = line[52:56].strip() + line[56:57].strip()
    else:
        name = line[12:16].strip()
        res = line[17:20].strip()
        chain = line[21:22].strip() or "_"
        number = line[22:26].strip() + line[26:27].strip()
    return name, res, f"{chain}:{number}"


def _strip_coordination_connectivity(
    pdb: str,
    *,
    coordination_residue: str,
    heme_residue: str,
    protein_donor_atom: str = "SG",
    heme_iron_atom: str = "FE",
):
    """Remove only the explicit Fe-donor connectivity record for rigid PDBQT conversion.

    Coordinates and atoms are untouched. The physical coordination geometry is
    checked before and after conversion. Internal HEM CONECT records are retained.
    """
    atoms = core.atoms(pdb)
    heme_selector = _meeko_selector(heme_residue)
    coord_selector = _meeko_selector(coordination_residue)

    donor = [
        a for a in atoms
        if _meeko_selector(_atom_id(a)) == coord_selector
        and a["name"].upper() == protein_donor_atom.upper()
    ]
    iron = [
        a for a in atoms
        if _meeko_selector(_atom_id(a)) == heme_selector
        and a["name"].upper() == heme_iron_atom.upper()
    ]
    donor_serial = int(donor[0]["line"][6:11]) if len(donor) == 1 else None
    iron_serial = int(iron[0]["line"][6:11]) if len(iron) == 1 else None

    kept = []
    omitted = []
    for line in pdb.splitlines():
        if line.startswith("LINK"):
            left = _link_endpoint(line, False)
            right = _link_endpoint(line, True)
            left_id = _meeko_selector(left[2])
            right_id = _meeko_selector(right[2])
            pair = {
                (left_id, left[0].upper()),
                (right_id, right[0].upper()),
            }
            wanted = {
                (coord_selector, protein_donor_atom.upper()),
                (heme_selector, heme_iron_atom.upper()),
            }
            if pair == wanted:
                omitted.append(line)
                continue

        if line.startswith("CONECT") and donor_serial is not None and iron_serial is not None:
            try:
                ids = [
                    int(line[i:i + 5])
                    for i in range(6, len(line), 5)
                    if line[i:i + 5].strip()
                ]
            except ValueError:
                ids = []
            if ids:
                source = ids[0]
                targets = []
                changed = False
                for target in ids[1:]:
                    if {source, target} == {donor_serial, iron_serial}:
                        changed = True
                        continue
                    targets.append(target)
                if changed:
                    omitted.append(line)
                    if targets:
                        kept.append("CONECT" + "".join(f"{n:5d}" for n in [source] + targets))
                    continue

        kept.append(line)

    return "\n".join(kept).rstrip() + "\n", omitted


def _pdbqt_atom_names(path: Path, residue_name: str):
    names = set()
    for line in Path(path).read_text(errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if len(line) >= 20 and line[17:20].strip().upper() == residue_name.upper():
            names.add(line[12:16].strip())
    return names


def _pdbqt_has_iron(path: Path):
    for line in Path(path).read_text(errors="replace").splitlines():
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
    coordination_template: str = DEFAULT_CYP450_THIOLATE_TEMPLATE,
    max_coordination_distance_A: float = 3.0,
):
    """Prepare a rigid CYP450 heme receptor with an explicit reviewed HEM template.

    The proximal cysteine is forced to Meeko's thiolate template (CYX- by
    default), HEM is forced to the packaged HEM_PANDOC template, and no residue
    is deleted to make preparation pass.
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

    template_path, template_sha256 = _copy_packaged_heme_template(
        heme_component,
        directory / f"{heme_component.upper()}_meeko_template.json",
    )

    heme_selector = _meeko_selector(before["heme_residue"])
    assignments = _set_assignment(
        template_assignments,
        coordination_residue,
        coordination_template,
    )
    assignments = _set_assignment(assignments, heme_selector, HEME_TEMPLATE_KEY)
    (directory / "meeko_template_assignments.txt").write_text(assignments + "\n")

    meeko_input, omitted_connectivity = _strip_coordination_connectivity(
        pdb,
        coordination_residue=coordination_residue,
        heme_residue=before["heme_residue"],
        protein_donor_atom=protein_donor_atom,
        heme_iron_atom=heme_iron_atom,
    )
    (directory / "receptor_for_meeko.pdb").write_text(meeko_input)
    (directory / "omitted_coordination_records.txt").write_text(
        "\n".join(omitted_connectivity)
        if omitted_connectivity
        else "No explicit Fe-donor LINK/CONECT record was present in the Meeko input.\n"
    )

    try:
        receptor_pdbqt = core.prepare_receptor(
            meeko_input,
            directory / "meeko",
            template_assignments=assignments,
            add_templates=[template_path],
        )
    except ValueError as exc:
        raise CuratedHemePreparationError(
            "Curated heme receptor conversion failed with the packaged HEM template "
            f"and {coordination_residue}={coordination_template}. No HEM atom or "
            f"protein residue was deleted. Review the Meeko diagnostics: {exc}"
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

    if after["heme_atom_count"] != before["heme_atom_count"]:
        raise CuratedHemePreparationError(
            "HEM atom count changed during receptor preparation "
            f"({before['heme_atom_count']} -> {after['heme_atom_count']})."
        )

    if not _pdbqt_has_iron(Path(receptor_pdbqt)):
        raise CuratedHemePreparationError(
            "The generated receptor PDBQT does not contain an Fe atom."
        )

    pdbqt_heme_names = _pdbqt_atom_names(Path(receptor_pdbqt), heme_component)
    missing_heme_heavy = sorted(
        set(before["heme_heavy_atom_names"]) - pdbqt_heme_names
    )
    if missing_heme_heavy:
        raise CuratedHemePreparationError(
            "The generated receptor PDBQT is missing HEM heavy atoms: "
            + ", ".join(missing_heme_heavy)
        )

    audit = {
        "mode": "curated_heme",
        "status": "prepared_curated_heme",
        "before": before,
        "after": after,
        "heme_retained_in_pdbqt": True,
        "heme_pdbqt_heavy_atoms_verified": True,
        "remote_residue_exclusion": False,
        "residue_deletion": False,
        "heme_template_key": HEME_TEMPLATE_KEY,
        "heme_template_assignment": f"{heme_selector}={HEME_TEMPLATE_KEY}",
        "coordination_template_assignment": (
            f"{_meeko_selector(coordination_residue)}={coordination_template}"
        ),
        "template_source": "PanDoc packaged reviewed HEM template",
        "template_file": str(template_path),
        "template_sha256": template_sha256,
        "coordination_connectivity_records_omitted_for_pdbqt_conversion": len(
            omitted_connectivity
        ),
        "coordination_geometry_preserved_by_distance_check": True,
        "validation_required": True,
        "validation_note": (
            "Successful receptor conversion does not establish docking validity. "
            "Redock the crystallographic ligand and evaluate fixed-frame heavy-atom RMSD "
            "before production docking."
        ),
        "upstream_notes": [
            "Meeko issue 207 documents HEM/Fe-containing cofactor CCD template failures.",
            "Meeko 0.8 supports explicit --add_templates and Fe-aware Gasteiger handling.",
        ],
    }
    (directory / "curated_heme_audit.json").write_text(json.dumps(audit, indent=2))
    return Path(receptor_pdbqt), audit
