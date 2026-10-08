from pandoc import heme_prep


def _atom(record, serial, name, res, chain, number, x, y, z, element):
    return (
        f"{record:<6}{serial:5d} {name:>4} {res:>3} {chain:1}{number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {element:>2}"
    )


def test_curated_heme_site_accepts_expected_fe_s_geometry():
    pdb = "\n".join([
        _atom("ATOM", 1, "SG", "CYS", "A", 437, 0.0, 0.0, 0.0, "S"),
        _atom("HETATM", 2, "FE", "HEM", "A", 600, 0.0, 0.0, 2.44, "FE"),
        _atom("HETATM", 3, "NA", "HEM", "A", 600, 1.0, 0.0, 2.44, "N"),
        "END",
    ])
    audit = heme_prep.validate_heme_site(
        pdb,
        coordination_residue="A:437",
        max_coordination_distance_A=3.0,
    )
    assert audit["heme_residue"] == "A:600:HEM"
    assert audit["coordination_distance_A"] == 2.44
    assert audit["heme_atom_count"] == 2


def test_curated_heme_site_rejects_lost_coordination():
    import pytest

    pdb = "\n".join([
        _atom("ATOM", 1, "SG", "CYS", "A", 437, 0.0, 0.0, 0.0, "S"),
        _atom("HETATM", 2, "FE", "HEM", "A", 600, 0.0, 0.0, 4.5, "FE"),
        "END",
    ])
    with pytest.raises(heme_prep.CuratedHemePreparationError, match="coordination check failed"):
        heme_prep.validate_heme_site(
            pdb,
            coordination_residue="A:437",
            max_coordination_distance_A=3.0,
        )


def test_curated_heme_site_requires_expected_cysteine_donor():
    import pytest

    pdb = "\n".join([
        _atom("ATOM", 1, "SG", "CYS", "A", 436, 0.0, 0.0, 0.0, "S"),
        _atom("HETATM", 2, "FE", "HEM", "A", 600, 0.0, 0.0, 2.44, "FE"),
        "END",
    ])
    with pytest.raises(heme_prep.CuratedHemePreparationError, match="Expected donor atom A:437:SG"):
        heme_prep.validate_heme_site(
            pdb,
            coordination_residue="A:437",
        )
