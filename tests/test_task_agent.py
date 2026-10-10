"""Stateful agent API and scientific execution contracts.

All external scientific and remote compute calls are faked; tests verify that
approval is required before any preparation or external submission.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import pytest

import api
from api import app
from pandoc import core, task_agent


CLIENT = TestClient(app)
FIXTURE = Path(__file__).parent / "browser" / "fixture_complex.pdb"


class FakeBackend:
    def __init__(self):
        self.submitted = []
        self.cleaned = False
        self.cancelled = False
        self.remote_state = "completed"

    def submit(self, root, config):
        self.submitted.append(config)
        return {"backend": "github-actions", "job_id": "b" * 32}

    def status(self, handle):
        return {"state": self.remote_state, "completed": 1, "total": 1}

    def materialize(self, handle, target):
        target.mkdir(parents=True, exist_ok=True)
        (target / "results.json").write_text(json.dumps([
            {"ligand_id": "private-ligand", "seed": 2026, "rank": 1,
             "score_kcal_mol": -7.2, "reference_rmsd_A": 1.2},
        ]))
        (target / "status.json").write_text(json.dumps({
            "state": "completed", "completed": 1, "total": 1
        }))
        return target

    def cleanup(self, handle):
        self.cleaned = True

    def cancel(self, handle):
        self.cancelled = True
        return True


@pytest.fixture
def agent_environment(tmp_path, monkeypatch):
    """Private, persistent task storage with deterministic local PDB fixture."""
    monkeypatch.setattr(api, "API_ROOT", tmp_path)
    monkeypatch.setattr(api, "API_KEY", "pilot-task-secret")
    fixture = FIXTURE.read_text()
    monkeypatch.setattr(task_agent.pdb_search, "download", lambda pdb: (
        "data_fixture", {"pdb_id": pdb, "type": "RCSB fixture"}
    ))
    monkeypatch.setattr(task_agent.core, "normalize_structure", lambda cif, suffix: fixture)

    def comparison(reference, smiles):
        return {"valid": smiles == "CCO", "message": "OK" if smiles == "CCO" else "Wrong chemistry"}

    monkeypatch.setattr(task_agent.core, "ligand_comparison", comparison)
    monkeypatch.setattr(task_agent.core, "reference_from_pdb", lambda reference, smiles: object())
    monkeypatch.setattr(task_agent.core, "repair_heavy_atoms", lambda pdb: pdb)

    def fake_receptor(pdb, path, template_assignments="", **kwargs):
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "receptor_prepared.pdb").write_text(pdb)
        (folder / "receptor.pdbqt").write_text("FAKE PDBQT")
        (folder / "preparation.log").write_text("OK")
        return folder / "receptor.pdbqt"

    def fake_ligand(mol, path):
        path = Path(path)
        path.write_text("REFERENCE PDBQT")
        path.with_suffix(".sdf").write_text("REFERENCE SDF")

    monkeypatch.setattr(task_agent.core, "prepare_receptor", fake_receptor)
    monkeypatch.setattr(task_agent.core, "write_ligand", fake_ligand)

    backend = FakeBackend()
    monkeypatch.setattr(api, "compute_backend", lambda require_github=True: backend)
    return {"root": tmp_path, "key": {"X-API-Key": "pilot-task-secret"}, "backend": backend}


def _start(env, instruction="Prepare PDB 1LF2 at pH 5.0 and validate by redocking"):
    result = CLIENT.post("/api/v1/agent/tasks", headers=env["key"],
                         json={"instruction": instruction})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def _selected(env, task_id):
    result = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/select", headers=env["key"],
                         json={"chains": ["A"], "reference_residue": "A:101:EOH"})
    assert result.status_code == 200, result.text
    return result.json()


def _prepared(env, task_id):
    result = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/prepare", headers=env["key"],
                         json={"reference_smiles": "CCO", "approved": True})
    assert result.status_code == 200, result.text
    return result.json()


def test_agent_api_fails_closed_without_authentication(agent_environment, monkeypatch):
    env = agent_environment
    monkeypatch.setattr(api, "API_KEY", "")
    request = {"instruction": "Inspect PDB 1LF2"}
    assert CLIENT.post("/api/v1/agent/tasks", json=request).status_code == 503
    monkeypatch.setattr(api, "API_KEY", "pilot-task-secret")
    assert CLIENT.post("/api/v1/agent/tasks", json=request).status_code == 401
    assert CLIENT.post("/api/v1/agent/tasks", headers={"X-API-Key": "wrong"},
                       json=request).status_code == 401


def test_pdb_is_explicit_and_inspection_persists(agent_environment):
    env = agent_environment
    task_id = _start(env)
    payload = CLIENT.get(f"/api/v1/agent/tasks/{task_id}", headers=env["key"])
    assert payload.status_code == 200
    view = payload.json()
    assert view["stage"] == "await_structure_review"
    assert view["pdb_id"] == "1LF2" and view["pH"] == 5.0
    assert view["goal"] == "redocking"
    assert "A" in view["receptor_chains"]
    assert view["reference_candidates"][0]["component"] == "EOH"
    folder = env["root"] / "agent_tasks" / task_id
    assert (folder / "structure.pdb").is_file()
    assert (folder / "inspection.json").is_file()
    assert "crystal_reference.pdb" not in [x.name for x in folder.iterdir()]


def test_missing_pdb_never_guesses_and_bad_id_is_rejected(agent_environment):
    env = agent_environment
    task_id = _start(env, "Prepare my favourite protein at pH 5")
    payload = CLIENT.get(f"/api/v1/agent/tasks/{task_id}", headers=env["key"]).json()
    assert payload["stage"] == "needs_pdb_id"
    assert payload["pdb_id"] is None
    assert CLIENT.get("/api/v1/agent/tasks/../../etc", headers=env["key"]).status_code in (404, 405)
    assert CLIENT.get("/api/v1/agent/tasks/not-a-task", headers=env["key"]).status_code == 422


def test_chemical_and_structure_approval_cannot_be_skipped(agent_environment):
    env = agent_environment
    task_id = _start(env)
    base = f"/api/v1/agent/tasks/{task_id}"
    refused = CLIENT.post(base + "/prepare", headers=env["key"], json={
        "reference_smiles": "CCO", "approved": True
    })
    assert refused.status_code == 422
    bad_ref = CLIENT.post(base + "/select", headers=env["key"], json={
        "chains": ["A"], "reference_residue": "A:999:UNK",
    })
    assert bad_ref.status_code == 422
    selected = _selected(env, task_id)
    assert selected["stage"] == "await_chemistry_review"
    denied = CLIENT.post(base + "/prepare", headers=env["key"], json={
        "reference_smiles": "CCO", "approved": False
    })
    assert denied.status_code == 422
    wrong = CLIENT.post(base + "/prepare", headers=env["key"], json={
        "reference_smiles": "N#N", "approved": True
    })
    assert wrong.status_code == 422
    assert CLIENT.get(base, headers=env["key"]).json()["stage"] == "await_chemistry_review"
    assert env["backend"].submitted == []


def test_approved_prep_then_one_redock_submission_then_analysis(agent_environment):
    env = agent_environment
    task_id = _start(env)
    _selected(env, task_id)
    prepared = _prepared(env, task_id)
    assert prepared["stage"] == "await_docking_approval"
    assert prepared["docking_proposal"]["seeds"] == [2026, 2027, 2028]
    folder = env["root"] / "agent_tasks" / task_id
    assert (folder / "prepared" / "reference.sdf").is_file()
    assert (folder / "crystal_reference.pdb").is_file()
    denied = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/redock",
                         headers=env["key"], json={"approved": False})
    assert denied.status_code == 422 and env["backend"].submitted == []
    running = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/redock",
                          headers=env["key"], json={"approved": True})
    assert running.status_code == 200, running.text
    assert running.json()["stage"] == "running"
    assert len(env["backend"].submitted) == 1
    config = env["backend"].submitted[0]
    assert config["reference"].endswith("/reference.sdf")
    assert config["ligands"][0]["path"].endswith("/reference.pdbqt")
    assert config["seeds"] == [2026, 2027, 2028]
    # Same request cannot launch duplicate expensive compute.
    repeat = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/redock",
                         headers=env["key"], json={"approved": True})
    assert repeat.status_code == 422 and len(env["backend"].submitted) == 1
    completed = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/refresh",
                            headers=env["key"])
    assert completed.status_code == 200, completed.text
    assert completed.json()["stage"] == "completed"
    assert completed.json()["summary"]["saved_pose_count"] == 1
    assert completed.json()["summary"]["compounds"][0]["best_reference_rmsd_A"] == 1.2
    assert env["backend"].cleaned is True
    result = CLIENT.get(f"/api/v1/agent/tasks/{task_id}/bundle", headers=env["key"])
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        files = archive.namelist()
        assert "redocking_summary.json" in files
        assert "crystal_reference.pdb" in files
        assert "agent_task.json" in files


def test_protonation_proposals_not_automatically_applied(agent_environment, monkeypatch):
    env = agent_environment
    task_id = _start(env)
    _selected(env, task_id)
    monkeypatch.setattr(task_agent.phprep, "predict_protein_states", lambda pdb, work, ph: (
        [{"residue": "A:10", "template": "ASH", "review_required": True}],
        "raw prediction",
    ))
    monkeypatch.setattr(task_agent.phprep, "template_assignments",
                        lambda rows: "A:10=ASH")
    response = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/protonation",
                           headers=env["key"])
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["review_required"] == ["A:10"]
    assert CLIENT.get(f"/api/v1/agent/tasks/{task_id}",
                      headers=env["key"]).json()["stage"] == "await_chemistry_review"
    # No auto-assigned templates or docking.
    assert not (env["root"] / "agent_tasks" / task_id / "chemistry.json").exists()


def test_backend_uncertainty_never_automatically_retries(agent_environment, monkeypatch):
    env = agent_environment
    task_id = _start(env)
    _selected(env, task_id)
    _prepared(env, task_id)

    def submitted_but_response_lost(root, config):
        env["backend"].submitted.append(config)
        raise RuntimeError("Network broke after remote dispatch")

    monkeypatch.setattr(env["backend"], "submit", submitted_but_response_lost)
    response = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/redock",
                           headers=env["key"], json={"approved": True})
    assert response.status_code == 422
    view = CLIENT.get(f"/api/v1/agent/tasks/{task_id}", headers=env["key"]).json()
    assert view["stage"] == "submission_uncertain"
    again = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/redock",
                        headers=env["key"], json={"approved": True})
    assert again.status_code == 422
    assert len(env["backend"].submitted) == 1



def test_ccd_and_ligand_microstate_proposals_do_not_prepare_without_review(
        agent_environment, monkeypatch):
    env = agent_environment
    task_id = _start(env)
    _selected(env, task_id)
    monkeypatch.setattr(task_agent.core, "fetch_ccd", lambda component: {
        "smiles": "CCO", "component": component,
        "source": "https://data.rcsb.org/rest/v1/core/chemcomp/EOH",
    })
    monkeypatch.setattr(task_agent.phprep, "enumerate_ligand_states",
                        lambda smiles, ph, n: [
                            {"index": 1, "smiles": "CCO", "formal_charge": 0,
                             "mol": object()},
                        ])
    response = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/ligand-options",
                           headers=env["key"])
    assert response.status_code == 200, response.text
    proposal = response.json()["options"]
    assert proposal["component"] == "EOH"
    assert proposal["microstates"][0]["smiles"] == "CCO"
    assert "mol" not in json.dumps(proposal)
    assert CLIENT.get(f"/api/v1/agent/tasks/{task_id}",
                      headers=env["key"]).json()["stage"] == "await_chemistry_review"
    assert not (env["root"] / "agent_tasks" / task_id / "chemistry.json").exists()
    cached = CLIENT.post(f"/api/v1/agent/tasks/{task_id}/ligand-options",
                         headers=env["key"])
    assert cached.status_code == 200 and cached.json()["cached"] is True
