import io
import json
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from pandoc import github_compute


def test_encrypted_job_payload_roundtrip(tmp_path):
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    reference = tmp_path / "reference.sdf"
    receptor.write_text("RECEPTOR")
    ligand.write_text("LIGAND")
    reference.write_text("REFERENCE")

    config = {
        "receptor": str(receptor),
        "ligands": [{"id": "ligand_001", "name": "test", "path": str(ligand)}],
        "reference": str(reference),
        "center": [1.0, 2.0, 3.0],
        "size": [20.0, 20.0, 20.0],
        "seeds": [2026],
        "exhaustiveness": 8,
        "poses": 9,
    }
    key = Fernet.generate_key().decode()
    encrypted = github_compute._payload(config, key)

    assert b"RECEPTOR" not in encrypted
    raw = github_compute.decrypt_payload(encrypted, key)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        portable = json.loads(archive.read("config.json"))
        assert archive.read("inputs/receptor.pdbqt") == b"RECEPTOR"
        assert archive.read("inputs/ligand_001.pdbqt") == b"LIGAND"
        assert archive.read("inputs/reference.sdf") == b"REFERENCE"
        assert portable["receptor"] == "inputs/receptor.pdbqt"
        assert portable["ligands"][0]["path"] == "inputs/ligand_001.pdbqt"
        assert portable["reference"] == "inputs/reference.sdf"
