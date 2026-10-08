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
