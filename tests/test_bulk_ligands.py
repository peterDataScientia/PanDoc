import csv
import io
import json
import zipfile
from pathlib import Path
import pytest
from pandoc import bulk_ligands


def test_csv_uses_requested_headers():
    data = b"CHEMBL_ID,Canonical_SMILES\nCHEMBL1,CCO\nCHEMBL2,c1ccccc1\n"
    rows = bulk_ligands.parse_csv(data, "CHEMBL_ID", "Canonical_SMILES")
    assert [r["id"] for r in rows] == ["CHEMBL1", "CHEMBL2"]
    assert rows[0]["smiles"] == "CCO"


def test_bulk_cap():
    data = ("id,smiles\n" + "A,CCO\n" * 10001).encode()
    with pytest.raises(ValueError, match="10,000"):
        bulk_ligands.parse_csv(data, "id", "smiles")


def test_missing_smiles_is_reported_not_dropped(tmp_path, monkeypatch):
    from pandoc import core
    def fake_molecule(smiles):
        return smiles
    def fake_write(mol, path):
        Path(path).write_text("ROOT\nATOM test ligand docking input\n" * 5)
    monkeypatch.setattr(core, "molecule", fake_molecule)
    monkeypatch.setattr(core, "write_ligand", fake_write)
    rows = [{"id": "one", "smiles": "CCO", "csv_line": 2},
            {"id": "two", "smiles": "", "csv_line": 3}]
    bulk_ligands.start(tmp_path, rows, 7.4)
    state = bulk_ligands.advance(tmp_path)
    assert state["status"] == "completed"
    assert (state["success"], state["failed"]) == (1, 1)
    packed = bulk_ligands.build_zip(tmp_path)
    with zipfile.ZipFile(packed) as z:
        assert "pdbqt/ligand_000001.pdbqt" in z.namelist()
        assert "pdbqt/ligand_000002.pdbqt" not in z.namelist()
        manifest = json.loads(z.read("preparation_manifest.json"))
        assert manifest["total"] == 2
        assert "two" in z.read("failed.csv").decode()
