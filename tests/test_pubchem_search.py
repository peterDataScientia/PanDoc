"""PubChem search is isolated from docking and validates compounds before adding."""
import pytest
from pandoc import pubchem_search


class FakeResponse:
    def __init__(self, status=200, props=None):
        self.status_code = status
        self.props = props or []
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))
    def json(self):
        return {"PropertyTable": {"Properties": self.props}}


def test_pubchem_name_search_uses_stereochemical_smiles(monkeypatch):
    seen = []
    def fake_get(url, **kwargs):
        seen.append(url)
        return FakeResponse(props=[{
            "CID": 2244, "Title": "Aspirin",
            "SMILES": "CC(=O)Oc1ccccc1C(=O)O",
            "MolecularFormula": "C9H8O4", "MolecularWeight": "180.16",
        }])
    monkeypatch.setattr(pubchem_search.requests, "get", fake_get)
    result = pubchem_search.find_compound("aspirin")
    assert result["cid"] == 2244
    assert result["smiles"]
    assert "/compound/name/aspirin/property/" in seen[0]
    assert result["url"].endswith("/2244")


def test_pubchem_cid_search_and_empty_search(monkeypatch):
    monkeypatch.setattr(pubchem_search.requests, "get", lambda url, **kw:
                        FakeResponse(props=[{"CID": 2244, "SMILES": "CCO"}]))
    assert pubchem_search.find_compound("2244")["cid"] == 2244
    with pytest.raises(pubchem_search.PubChemLookupError):
        pubchem_search.find_compound("  ")


def test_pubchem_not_found_and_invalid_structure(monkeypatch):
    monkeypatch.setattr(pubchem_search.requests, "get", lambda url, **kw: FakeResponse(status=404))
    with pytest.raises(pubchem_search.PubChemLookupError, match="No PubChem"):
        pubchem_search.find_compound("missing-name")
    monkeypatch.setattr(pubchem_search.requests, "get", lambda url, **kw:
                        FakeResponse(props=[{"CID": 2, "SMILES": "garbage!"}]))
    with pytest.raises(pubchem_search.PubChemLookupError, match="RDKit"):
        pubchem_search.find_compound("2")


def test_pubchem_search_details_render_as_table_columns(monkeypatch):
    monkeypatch.setattr(pubchem_search.requests, "get", lambda url, **kwargs:
        FakeResponse(props=[{
            "CID": 2244, "Title": "Aspirin",
            "SMILES": "CC(=O)Oc1ccccc1C(=O)O",
            "MolecularFormula": "C9H8O4", "MolecularWeight": "180.16",
            "XLogP": 1.2, "TPSA": 63.6, "HBondDonorCount": 1,
            "HBondAcceptorCount": 4, "RotatableBondCount": 3,
            "IUPACName": "2-acetyloxybenzoic acid",
        }]))
    result = pubchem_search.find_compound("2244")
    row = pubchem_search.compound_details_row(result)
    assert row["CID"] == 2244
    assert row["MW (g/mol)"] == "180.16"
    assert row["XLogP"] == 1.2
    assert row["TPSA (Å²)"] == 63.6
    assert row["HBD"] == 1 and row["HBA"] == 4
    assert row["SMILES"] == result["smiles"]
    assert row["PubChem"] == "https://pubchem.ncbi.nlm.nih.gov/compound/2244"


def test_pubchem_missing_optional_properties_remain_visible():
    row = pubchem_search.compound_details_row({
        "cid": 1, "name": "Ligand", "smiles": "CCO",
        "url": "https://pubchem.ncbi.nlm.nih.gov/compound/1",
    })
    assert row["Molecular formula"] == "—"
    assert row["XLogP"] is None
    assert row["IUPAC name"] == "—"
