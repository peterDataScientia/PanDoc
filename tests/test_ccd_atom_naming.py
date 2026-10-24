"""CCD labels must not be conflated with ligand chemical completeness."""
from pandoc import core


def _pdb(names_and_elements):
    lines = []
    for i, (name, elem) in enumerate(names_and_elements, 1):
        lines.append(
            f"HETATM{i:5d} {name:4s} LIG A   1    "
            f"{float(i):8.3f}{2.0:8.3f}{3.0:8.3f}"
            f"{1.0:6.2f}{20.0:6.2f}          {elem:>2s}"
        )
    return "\n".join(lines) + "\nEND\n"


def test_same_chemistry_different_atom_names():
    ref = _pdb([("O2", "O"), ("C1", "C")])
    ccd = dict(heavy_atom_names=["O1", "C1"], heavy_atom_elements=["O", "C"])
    result = core.ccd_atom_name_check(ref, ccd)
    assert result["status"] == "naming_difference"
    assert result["missing"] == ["O1"]
    assert result["element_differences"] == {}


def test_truly_missing_oxygen():
    ref = _pdb([("C1", "C")])
    ccd = dict(heavy_atom_names=["O1", "C1"], heavy_atom_elements=["O", "C"])
    result = core.ccd_atom_name_check(ref, ccd)
    assert result["status"] == "composition_mismatch"
    assert result["element_differences"]["O"] == {"ccd": 1, "crystal": 0}


def test_no_element_metadata_does_not_claim_missing_atom():
    ref = _pdb([("O2", "O"), ("C1", "C")])
    ccd = dict(heavy_atom_names=["O1", "C1"])
    result = core.ccd_atom_name_check(ref, ccd)
    assert result["status"] in ("names_only", "unavailable")
