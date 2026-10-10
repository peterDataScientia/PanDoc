"""Continuous scientific task-agent conversation: scientific grounding and gates."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import api
from pandoc import agent_conversation, task_agent


@pytest.fixture
def task(tmp_path):
    ident = "a" * 32
    folder = tmp_path / "agent_tasks" / ident
    folder.mkdir(parents=True)
    content = {
        "id": ident, "stage": "await_structure_review", "goal": "inspection",
        "pdb_id": "1LF2", "pH": None, "instruction": "Inspect PDB 1LF2",
        "audit": [], "created_utc": task_agent._now(),
    }
    task_agent._write(folder, content, "task_created")
    report = {
        "receptor_chains": ["A", "B"],
        "reference_candidates": [
            {"residue": "A:501:R37", "component": "R37", "heavy_atoms": 24},
            {"residue": "A:502:SO4", "component": "SO4", "heavy_atoms": 5},
        ],
        "components": [
            {"residue": "A:1:ASP", "chain": "A", "name": "ASP", "kind": "Protein", "heavy_atoms": 8},
            {"residue": "B:2:GLY", "chain": "B", "name": "GLY", "kind": "Protein", "heavy_atoms": 4},
            {"residue": "A:501:R37", "chain": "A", "name": "R37", "kind": "Other component", "heavy_atoms": 24},
            {"residue": "A:502:SO4", "chain": "A", "name": "SO4", "kind": "Other component", "heavy_atoms": 5},
        ],
        "alternates": [], "connections": [],
        "issues": [{"severity": "Warning", "residue": "A:1:ASP",
                    "problem": "Unmodeled side-chain atom", "action": "Review"}],
        "atom_count": 888, "source": {"pdb_id": "1LF2"},
    }
    (folder / "inspection.json").write_text(json.dumps(report))
    return tmp_path, ident, folder


def test_followups_use_recorded_ligands_and_persist(task):
    root, ident, folder = task
    answer = agent_conversation.respond(root, ident, "What did you find?")
    assert "1LF2" in answer and "888" in answer
    assert "R37" in answer and "SO4" in answer
    ligand = agent_conversation.respond(root, ident, "Which ligand should I use?")
    assert "not ranked docking recommendations" in ligand
    assert len(agent_conversation.history(root, ident)) == 2
    assert (folder / "conversation.json").exists()
    assert "R37" in agent_conversation.opening(root, ident)
    assert task_agent.describe(root, ident)["stage"] == "await_structure_review"


def test_task_followup_upgrades_goal_and_ph_but_does_not_approve(task):
    root, ident, folder = task
    (folder / "protonation.json").write_text('{"pH":7.0}')
    (folder / "ligand_options.json").write_text('{"pH":7.0}')
    result = agent_conversation.respond(root, ident, "Prepare it at pH 5.0")
    current = task_agent.describe(root, ident)
    assert "preparation at pH 5" in result
    assert current["goal"] == "preparation"
    assert current["pH"] == 5.0
    assert current["stage"] == "await_structure_review"
    assert not (folder / "protonation.json").exists()
    assert not (folder / "ligand_options.json").exists()
    assert not (folder / "selection.json").exists()
    assert not (folder / "docking_config.json").exists()
    revised = json.loads((folder / "task.json").read_text())
    assert any(x["action"] == "intent_revised" for x in revised["audit"])

    next_answer = agent_conversation.respond(root, ident, "Okay continue")
    assert "review" in next_answer.lower()
    assert len(agent_conversation.history(root, ident)) == 2
    assert task_agent.describe(root, ident)["stage"] == "await_structure_review"


def test_unrelated_pdb_and_questions_do_not_replace_current_structure(task):
    root, ident, folder = task
    text = agent_conversation.respond(root, ident, "How should I prepare this?")
    assert "1LF2" in text or "Review" in text
    assert task_agent.describe(root, ident)["goal"] == "inspection"
    answer = agent_conversation.respond(root, ident, "Prepare PDB 2XYZ at pH 5")
    assert "different PDB" in answer
    assert task_agent.describe(root, ident)["pdb_id"] == "1LF2"


def test_read_only_actions_require_appropriate_stage(task, monkeypatch):
    root, ident, folder = task
    invoked = []
    monkeypatch.setattr(task_agent, "protonation",
                        lambda *a: invoked.append("pka") or {"proposal": {}})
    result = agent_conversation.respond(root, ident, "Run PROPKA now")
    assert "requires a reviewed receptor" in result
    assert invoked == []
    stored = json.loads((folder / "task.json").read_text())
    stored["stage"] = "await_chemistry_review"
    stored["pH"] = 5.0
    task_agent._write(folder, stored)
    result = agent_conversation.respond(root, ident, "Run PROPKA now")
    assert "PROPKA predictions are now recorded" in result
    assert invoked == ["pka"]
    assert not (folder / "chemistry.json").exists()
    assert agent_conversation.respond(root, ident, "Yes, launch docking now")
    assert not (folder / "docking_config.json").exists()


def test_task_evidence_goes_to_groq_without_coordinates(task, monkeypatch):
    root, ident, folder = task
    import groq
    recorded = []

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="I found two deposited candidate components, R37 and SO4."
            ))])

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    first = agent_conversation.respond(root, ident, "What was found?", api_key="test")
    second = agent_conversation.respond(root, ident, "What about SO4?", api_key="test")
    assert "R37" in first and "SO4" in second
    assert len(recorded) == 2
    messages = recorded[-1]["messages"]
    wire = json.dumps(messages)
    assert "888" in wire and "R37" in wire and "SO4" in wire
    assert "What was found?" in wire and first in wire
    assert "secret_token" not in wire
    assert "xyz" not in wire
    assert "tools" not in recorded[0]  # Model cannot execute arbitrary mutation tools


def test_failed_model_falls_back_to_grounded_evidence(task, monkeypatch):
    root, ident, folder = task
    import groq
    monkeypatch.setattr(groq, "Groq",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("quota")))
    result = agent_conversation.respond(root, ident, "What did you find?", api_key="broken")
    assert "R37" in result
    assert "Model unavailable" in result
    assert len(agent_conversation.history(root, ident)) == 1


def test_authenticated_rest_chat_followup_and_history(task, monkeypatch):
    root, ident, folder = task
    monkeypatch.setattr(api, "API_ROOT", root)
    monkeypatch.setattr(api, "API_KEY", "agent-secret")
    client = TestClient(api.app)
    url = f"/api/v1/agent/tasks/{ident}"
    assert client.post(url + "/chat", json={"message": "What did you find?"}).status_code == 401
    headers = {"X-API-Key": "agent-secret"}
    response = client.post(url + "/chat", headers=headers, json={"message": "What did you find?"})
    assert response.status_code == 200, response.text
    assert "R37" in response.json()["answer"]
    assert response.json()["task"]["stage"] == "await_structure_review"
    transcript = client.get(url + "/conversation", headers=headers)
    assert transcript.status_code == 200
    assert len(transcript.json()["messages"]) == 1
    assert client.post(url + "/chat", headers=headers,
                       json={"message": "x" * 2100}).status_code == 422


def test_polite_conversation_controls_only_safe_explicit_actions(task, monkeypatch):
    root, ident, folder = task
    observed = []
    monkeypatch.setattr(task_agent, "protonation",
                        lambda *a: observed.append("pka") or {"proposal": {}})
    # A general how-to question must not run calculations.
    agent_conversation.respond(root, ident, "How would I run PROPKA?")
    assert observed == []
    record = json.loads((folder / "task.json").read_text())
    record["stage"] = "await_chemistry_review"
    record["goal"] = "preparation"
    record["pH"] = 5.0
    task_agent._write(folder, record)
    answer = agent_conversation.respond(root, ident,
                                        "Could you please run PROPKA now?")
    assert "now recorded" in answer
    assert observed == ["pka"]
    agent_conversation.respond(root, ident, "Please don't run PROPKA")
    agent_conversation.respond(root, ident, "Okay continue")
    assert observed == ["pka"]
    assert not (folder / "chemistry.json").exists()
    assert not (folder / "docking_config.json").exists()


def test_researcher_can_change_planned_ph_without_restarting(task):
    root, ident, folder = task
    agent_conversation.respond(root, ident, "Can we prepare it at pH 5.0?")
    (folder / "protonation.json").write_text('{"pH":5.0}')
    (folder / "ligand_options.json").write_text('{"pH":5.0}')
    answer = agent_conversation.respond(root, ident, "Could you change our pH to 6.0?")
    view = task_agent.describe(root, ident)
    assert "6.0" in answer
    assert view["goal"] == "preparation"
    assert view["pH"] == 6.0
    assert not (folder / "protonation.json").exists()
    assert not (folder / "ligand_options.json").exists()
    assert not (folder / "chemistry.json").exists()
    assert not (folder / "docking_config.json").exists()
