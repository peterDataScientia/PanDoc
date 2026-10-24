"""Regression checks for BRD4 3MXF terminal GLU chemistry."""
from pandoc.core import normalize_terminal_templates


def atom(name, res, number, chain="A"):
    return (
        f"ATOM      1 {name:4s} {res:>3s} {chain}{number:4d}    "
        f"{1.0:8.3f}{2.0:8.3f}{3.0:8.3f}{1.0:6.2f}{20.0:6.2f}"
        f"          {name[0]:>2s}  "
    )


def test_terminal_glutamate_maps_to_cglu():
    pdb = "\n".join([atom("CA", "GLU", 168), atom("OXT", "GLU", 168)]) + "\n"
    assert normalize_terminal_templates(pdb, "A:168=GLU,A:45=HID") == "A:168=CGLU,A:45=HID"
    assert normalize_terminal_templates(pdb, "A:168=GLH") == "A:168=CGLH"


def test_internal_glutamate_not_changed():
    pdb = atom("CA", "GLU", 168) + "\n"
    assert normalize_terminal_templates(pdb, "A:168=GLU") == "A:168=GLU"


def test_existing_terminal_template_preserved():
    pdb = atom("OXT", "GLU", 168) + "\n"
    assert normalize_terminal_templates(pdb, "A:168=CGLU") == "A:168=CGLU"
