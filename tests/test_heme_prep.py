import json

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

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


def test_packaged_heme_template_has_expected_chemistry():
    payload = json.loads(heme_prep.PACKAGED_HEM_TEMPLATE.read_text())
    template = payload["residue_templates"][heme_prep.HEME_TEMPLATE_KEY]
    ps = Chem.SmilesParserParams()
    ps.removeHs = False
    mol = Chem.MolFromSmiles(template["smiles"], ps)

    assert mol is not None
    assert mol.GetNumAtoms() == 73
    assert sum(a.GetAtomicNum() != 1 for a in mol.GetAtoms()) == 43
    assert rdMolDescriptors.CalcMolFormula(mol) == "C34H30FeN4O4-2"
    assert Chem.GetFormalCharge(mol) == -2
    assert len(template["atom_name"]) == 73
    assert all(a.GetNumImplicitHs() == 0 for a in mol.GetAtoms())
    assert "H2A" not in template["atom_name"]
    assert "H2D" not in template["atom_name"]


def test_packaged_heme_template_loads_into_meeko():
    from meeko.polymer import ResidueChemTemplates

    templates = ResidueChemTemplates.create_from_defaults()
    templates.add_json_file(str(heme_prep.PACKAGED_HEM_TEMPLATE))

    assert heme_prep.HEME_TEMPLATE_KEY in templates.residue_templates
    assert heme_prep.HEME_TEMPLATE_KEY in templates.ambiguous["HEM"]


def test_heme_assignments_force_static_template_and_proximal_thiolate():
    assignments = "A:62=HIE,A:437=CYS,A:475=HIP"
    assignments = heme_prep._set_assignment(assignments, "A:437", "CYX-")
    assignments = heme_prep._set_assignment(assignments, "A:600:HEM", "HEM_PANDOC")

    assert assignments.split(",") == [
        "A:62=HIE",
        "A:437=CYX-",
        "A:475=HIP",
        "A:600=HEM_PANDOC",
    ]


def test_only_fe_s_coordination_connectivity_is_omitted():
    link = "LINK         SG  CYS A 437                FE   HEM A 600     1555   1555  2.44"
    pdb = "\n".join([
        link,
        _atom("ATOM", 1, "SG", "CYS", "A", 437, 0.0, 0.0, 0.0, "S"),
        _atom("HETATM", 2, "FE", "HEM", "A", 600, 0.0, 0.0, 2.44, "FE"),
        _atom("HETATM", 3, "NA", "HEM", "A", 600, 1.0, 0.0, 2.44, "N"),
        "CONECT    1    2",
        "CONECT    2    1    3",
        "CONECT    3    2",
        "END",
    ])

    cleaned, omitted = heme_prep._strip_coordination_connectivity(
        pdb,
        coordination_residue="A:437",
        heme_residue="A:600:HEM",
    )

    assert link not in cleaned
    assert "CONECT    1    2" not in cleaned
    assert "CONECT    2    1    3" not in cleaned
    assert "CONECT    2    3" in cleaned
    assert "CONECT    3    2" in cleaned
    assert len(omitted) == 3
