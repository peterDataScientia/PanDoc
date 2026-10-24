"""Regression checks for previously failed BRD4 CSV ligands."""
from rdkit import Chem
import pytest
from pandoc import ligand_recovery


@pytest.mark.parametrize("smiles,removed", [
    ("CCN(CC)c1ccccc1.[Cl-]", "[Cl-]"),
    ("CCn1cnc2c1c(=O)[nH]c(=O)n2Cc1ccc(CN)cc1.O=C(O)C(F)(F)F",
     "O=C(O)C(F)(F)F"),
    ("CNC(=O)c1cc(C(=O)NC2CC2)cc(Cc2cccc3c2CCN3)n1.Cl", "Cl"),
])
def test_salts_keep_organic_drug_and_audit_counterion(smiles, removed):
    mol, audit = ligand_recovery.prepare(smiles)
    assert mol.GetNumConformers() == 1
    assert len(Chem.GetMolFrags(Chem.RemoveHs(mol))) == 1
    assert removed in audit["removed_counterions"]
    assert audit["chemistry_modified"] is True
    assert audit["original_smiles"] != audit["prepared_smiles"]


def test_ambiguous_two_drugs_not_silently_stripped():
    with pytest.raises(ValueError, match="Ambiguous"):
        ligand_recovery.prepare("CCOc1ccccc1.CCN(CC)CC")


def test_nonapproved_counterion_requires_review():
    with pytest.raises(ValueError, match="Ambiguous|Nonstandard"):
        ligand_recovery.prepare("CCOc1ccccc1.CCCCC")


def test_unmodified_single_component_is_traceable():
    mol, audit = ligand_recovery.prepare("CCOc1ccccc1")
    assert mol.GetNumConformers() == 1
    assert audit["removed_counterions"] == []
    assert audit["chemistry_modified"] is False
