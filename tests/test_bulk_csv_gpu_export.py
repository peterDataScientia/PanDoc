"""CSV-based GPU-only docking input preparation (no receptor or docking needed)."""
import csv
import io
import json
import zipfile

import pytest

from pandoc import bulk_ligands


def _csv(n):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["compound_id", "SMILES"])
    writer.writerows((f"BRD4_{i:05d}", "CCO") for i in range(n))
    return out.getvalue().encode()


def test_accepts_ten_thousand_rows_without_dropping_ids():
    parsed = bulk_ligands.parse_csv(_csv(10000), "compound_id", "SMILES")
    assert len(parsed) == 10000
    assert parsed[0]["id"] == "BRD4_00000"
    assert parsed[-1]["id"] == "BRD4_09999"


def test_rejects_more_than_ten_thousand():
    with pytest.raises(ValueError, match="10,000"):
        bulk_ligands.parse_csv(_csv(10001), "compound_id", "SMILES")


def test_export_has_manifest_success_and_failed_lists(tmp_path, monkeypatch):
    from pathlib import Path
    from pandoc import core

    def prepare(smiles):
        if smiles == "BAD":
            raise ValueError("Invalid SMILES")
        return smiles

    def output(mol, path):
        Path(path).write_text("REMARK PREPARED TEST PDBQT\n" * 8)

    monkeypatch.setattr(core, "molecule", lambda smiles: prepare(smiles))
    monkeypatch.setattr(core, "write_ligand", output)
    records = [{"id": "a", "smiles": "CCO", "csv_line": 2},
               {"id": "b", "smiles": "BAD", "csv_line": 3}]
    bulk_ligands.start(tmp_path / "job", records, 7.4)
    state = bulk_ligands.advance(tmp_path / "job", batch_size=40)
    assert (state["success"], state["failed"], state["completed"]) == (1, 1, 2)
    export = bulk_ligands.build_zip(tmp_path / "job")
    with zipfile.ZipFile(export) as z:
        names = set(z.namelist())
        assert "pdbqt/ligand_000001.pdbqt" in names
        assert "pdbqt/ligand_000002.pdbqt" not in names
        assert {"success.csv", "failed.csv", "preparation_manifest.json"} <= names
        manifest = json.loads(z.read("preparation_manifest.json"))
        assert manifest["pH_context"] == 7.4
        assert manifest["protonation"].startswith("SMILES as supplied")
