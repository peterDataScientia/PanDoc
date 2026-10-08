import math

import pytest

from pandoc import heme_prep


def _atom(record, serial, name, res, chain, number, x, y, z, element):
    return (
        f"{record:<6}{serial:5d} {name:>4} {res:>3} {chain:1}{number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {element:>2}"
    )


def _minimal_heme_complex(include_s_h=False, heme_shift=0.0):
    lines = [
        _atom("ATOM", 1, "N", "CYS", "A", 437, -1.2, 0.0, 0.0, "N"),
        _atom("ATOM", 2, "CA", "CYS", "A", 437, 0.0, 0.0, 0.0, "C"),
        _atom("ATOM", 3, "CB", "CYS", "A", 437, 0.8, 0.0, 0.0, "C"),
        _atom("ATOM", 4, "SG", "CYS", "A", 437, 1.8, 0.0, 0.0, "S"),
    ]
    if include_s_h:
        lines.append(_atom("ATOM", 5, "HG", "CYS", "A", 437, 2.95, 0.0, 0.0, "H"))
        serial = 6
    else:
        serial = 5
    lines.extend([
        _atom("HETATM", serial, "FE", "HEM", "A", 600, 4.24 + heme_shift, 0.0, 0.0, "FE"),
        _atom("HETATM", serial + 1, "NA", "HEM", "A", 600, 4.24 + heme_shift, 1.9, 0.0, "N"),
        "END",
    ])
    return "\n".join(lines) + "\n"


def test_curated_heme_site_accepts_expected_fe_s_geometry():
    pdb = _minimal_heme_complex()
    audit = heme_prep.validate_heme_site(
        pdb,
        coordination_residue="A:437",
        max_coordination_distance_A=3.0,
    )
    assert audit["heme_residue"] == "A:600:HEM"
    assert math.isclose(audit["coordination_distance_A"], 2.44, abs_tol=0.001)
    assert audit["heme_atom_count"] == 2


def test_curated_heme_site_rejects_lost_coordination():
    pdb = _minimal_heme_complex(heme_shift=2.0)
    with pytest.raises(
        heme_prep.CuratedHemePreparationError,
        match="coordination check failed",
    ):
        heme_prep.validate_heme_site(
            pdb,
            coordination_residue="A:437",
            max_coordination_distance_A=3.0,
        )


def test_curated_heme_site_requires_expected_cysteine_donor():
    pdb = _minimal_heme_complex().replace("CYS A 437", "CYS A 436")
    with pytest.raises(
        heme_prep.CuratedHemePreparationError,
        match="Expected donor atom A:437:SG",
    ):
        heme_prep.validate_heme_site(
            pdb,
            coordination_residue="A:437",
        )


def test_reviewed_assignment_forces_proximal_cysteine_thiolate():
    assignments = "A:62=HIE,A:437=CYS,A:475=HIP"
    result = heme_prep._set_assignment(assignments, "A:437", "CYX-")
    assert result.split(",") == [
        "A:62=HIE",
        "A:437=CYX-",
        "A:475=HIP",
    ]


def test_thiolate_check_rejects_sulfur_bound_hydrogen():
    with pytest.raises(
        heme_prep.CuratedHemePreparationError,
        match="still protonated",
    ):
        heme_prep._validate_thiolate_donor(
            _minimal_heme_complex(include_s_h=True),
            coordination_residue="A:437",
        )


def test_thiolate_check_accepts_deprotonated_sulfur():
    audit = heme_prep._validate_thiolate_donor(
        _minimal_heme_complex(include_s_h=False),
        coordination_residue="A:437",
    )
    assert audit["thiolate_verified"] is True
    assert audit["sulfur_bound_hydrogen_count"] == 0


def test_heme_component_is_kept_separate_from_protein():
    pdb = _minimal_heme_complex()
    protein = heme_prep._protein_without_component(pdb, "HEM")
    heme = heme_prep._component_only(pdb, "HEM")

    assert " HEM " not in protein
    assert " CYS " in protein
    assert " HEM " in heme
    assert " CYS " not in heme
    assert " FE " in heme


def test_merge_retains_crystallographic_heme_coordinates_exactly():
    source = _minimal_heme_complex()
    heme = heme_prep._component_only(source, "HEM")
    protein = heme_prep._protein_without_component(source, "HEM")
    merged = heme_prep._merge_prepared_protein_and_heme(protein, heme)

    source_heme_lines = [
        line for line in heme.splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
    ]
    merged_heme_lines = [
        line for line in merged.splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
        and line[17:20].strip() == "HEM"
    ]
    assert merged_heme_lines == source_heme_lines


def test_pdbqt_validation_requires_retained_heme_and_fe_geometry(tmp_path):
    pdb = _minimal_heme_complex()
    before = heme_prep.validate_heme_site(
        pdb,
        coordination_residue="A:437",
    )
    path = tmp_path / "receptor.pdbqt"
    path.write_text(pdb)

    audit = heme_prep._validate_pdbqt_heme(path, before)
    assert audit["heme_atom_count_in_pdbqt"] == 2
    assert audit["iron_autodock_type"].upper() == "FE"
    assert math.isclose(audit["coordination_distance_A"], 2.44, abs_tol=0.001)



def test_adt_name_alignment_restores_arginine_and_tryptophan_heavy_atoms():
    """Legacy ADT must not read NH1/NH2/CH2 as hydrogen names."""
    bad = [
        _atom("ATOM", 1, "NH1", "ARG", "A", 115, 1, 2, 3, "N"),
        _atom("ATOM", 2, "NH2", "ARG", "A", 115, 2, 3, 4, "N"),
        _atom("ATOM", 3, "CH2", "TRP", "A", 224, 3, 4, 5, "C"),
    ]
    bad = [line[:12] + name.ljust(4) + line[16:]
           for name, line in zip(("NH1", "NH2", "CH2"), bad)]
    heme = _atom("HETATM", 4, "FE", "HEM", "A", 600, 4, 4, 4, "FE")
    raw = "\n".join(bad + [heme, "END"]) + "\n"
    fixed = heme_prep._align_single_element_pdb_names(raw)
    values = [line for line in fixed.splitlines() if line.startswith("ATOM")]
    assert [line[12:16] for line in values] == [" NH1", " NH2", " CH2"]
    assert [line[30:54] for line in values] == [line[30:54] for line in bad]
    assert heme in fixed


def test_adt_audit_rejects_heavy_atom_mistyped_as_hydrogen(tmp_path):
    source = _atom("ATOM", 1, "NH1", "ARG", "A", 115, 1, 2, 3, "N") + "\n"
    incorrect = _atom("ATOM", 1, "H1N", "ARG", "A", 115, 1, 2, 3, "H") + " HD\n"
    output = tmp_path / "bad_receptor.pdbqt"
    output.write_text(incorrect)
    with pytest.raises(
        heme_prep.CuratedHemePreparationError,
        match="heavy-atom identity/type/position errors",
    ):
        heme_prep.validate_pdbqt_heavy_atoms(source, output)


def test_adt_audit_accepts_preserved_element_names_and_coordinates(tmp_path):
    source = "\n".join([
        _atom("ATOM", 1, "NH1", "ARG", "A", 115, 1, 2, 3, "N"),
        _atom("ATOM", 2, "CH2", "TRP", "A", 224, 3, 4, 5, "C"),
    ]) + "\n"
    output = tmp_path / "valid_receptor.pdbqt"
    output.write_text(source)
    report = heme_prep.validate_pdbqt_heavy_atoms(source, output)
    assert report["all_receptor_heavy_atom_count"] == 2
    assert report["all_receptor_heavy_atom_types_verified"]
