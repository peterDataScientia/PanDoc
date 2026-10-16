"""Fast scientific smoke test for the Vina -> Meeko -> RMSD -> SDF pipeline."""
import json

from rdkit import Chem
from rdkit.Chem import AllChem

from pandoc import core
from pandoc.worker import run, format_vina_affinities


def test_vina_search_finishes_postprocessing_and_saves_results(tmp_path, capsys):
    peptide = Chem.AddHs(Chem.MolFromSequence("AG"))
    assert AllChem.EmbedMolecule(peptide, randomSeed=2026) == 0
    AllChem.MMFFOptimizeMolecule(peptide)
    receptor = core.prepare_receptor(
        Chem.MolToPDBBlock(Chem.RemoveHs(peptide)), tmp_path / "prepared"
    )
    ligand = core.molecule(smiles="CCO")
    ligand_path = tmp_path / "ethanol.pdbqt"
    core.write_ligand(ligand, ligand_path)

    job = tmp_path / "job"
    job.mkdir()
    config = {
        "receptor": str(receptor), "center": [0, 0, 0],
        "size": [12, 12, 12], "exhaustiveness": 1,
        "poses": 2, "seeds": [2026], "cpu": 1,
        "reference": str(tmp_path / "ethanol.sdf"),
        "ligands": [{"id": "ethanol", "name": "Ethanol", "path": str(ligand_path)}],
    }
    (job / "config.json").write_text(json.dumps(config))
    run(job)

    status = json.loads((job / "status.json").read_text())
    assert status["state"] == "completed", status
    assert status["stage"] == "completed"
    assert status["completed"] == status["total"] == 1
    rows = json.loads((job / "results.json").read_text())
    assert rows
    assert all(row["reference_rmsd_A"] >= 0 for row in rows)
    assert all(mol is not None for mol in Chem.SDMolSupplier(str(job / "ethanol_seed2026.sdf")))
    log = capsys.readouterr().out
    assert "Vina search returned; exporting docked PDBQT poses" in log
    assert "Scores retrieved; reconstructing RDKit conformers" in log
    assert "Completed docking search 1/1" in log
    assert "=== Vina docking scores | Ethanol | seed 2026 ===" in log
    assert "Affinity (kcal/mol)" in log
    assert "Best affinity:" in log
    assert "Best docking score across all searches:" in log
    assert f"{float(rows[0]['score_kcal_mol']):.3f}" in log
    assert "Computer B performance mode" not in log
    assert "serialized shared-map reuse disabled" in log


def test_vina_energy_table_uses_actual_pose_energies_and_correct_units():
    energies = [
        [-7.126, -8.0, 0.0, 0.0, 0.0],
        [-6.438, -7.2, 0.0, 0.0, 0.0],
        [-8.213, -8.8, 0.0, 0.0, 0.0],
    ]
    output = format_vina_affinities(energies, "Reference ligand", 2026)
    assert "Reference ligand | seed 2026" in output
    assert "Affinity (kcal/mol)" in output
    assert "   1        -7.126" in output
    assert "   2        -6.438" in output
    assert "   3        -8.213" in output
    assert "Best affinity: -8.213 kcal/mol (mode 3)" in output
    assert output.count("\\n") == 5
    assert "RMSD" not in output  # No fabricated pose-to-pose RMSDs.

def test_vina_score_formatter_has_no_fake_default_score():
    output = format_vina_affinities([], "Ligand A", 2027)
    assert "returned no pose affinities" in output
    assert "Best affinity" not in output
