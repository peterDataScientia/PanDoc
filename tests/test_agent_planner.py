"""Groq structured planning cannot bypass deterministic scientific review."""
from types import SimpleNamespace
import json

from pandoc import agent_planner


def test_deterministic_plan_requires_explicit_pdb():
    plan = agent_planner.plan_request("Prepare PDB 1LF2 at pH 5.0 and validate by redocking")
    assert plan["pdb_id"] == "1LF2"
    assert plan["pH"] == 5.0
    assert plan["goal"] == "redocking"
    assert plan["source"] == "deterministic"
    assert "researcher_review_chemistry" in plan["steps"]
    assert plan["steps"].index("researcher_approve_redocking") < plan["steps"].index("submit_redocking")
    assert agent_planner.plan_request("Please find my favourite receptor")["pdb_id"] is None


def test_groq_planning_adds_only_bounded_prose(monkeypatch):
    import groq
    captured = []

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            captured.append(kwargs)
            # A malicious/incorrect response cannot change the canonical steps.
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({
                    "summary": "Inspect 1LF2 then review and prepare for docking.",
                    "scientific_caution": "All chemistry requires review.",
                    "pdb_id": "9XYZ",
                    "steps": ["delete_repo", "submit_without_approval"],
                })
            ))])

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    plan = agent_planner.plan_request(
        "Prepare PDB 1LF2 at pH 5.0 and validate by redocking", api_key="fake"
    )
    assert plan["source"] == "groq_structured"
    assert plan["pdb_id"] == "1LF2"
    assert plan["pH"] == 5.0
    assert plan["steps"] == agent_planner.STEP_LIBRARY["redocking"]
    assert "delete_repo" not in json.dumps(plan)
    assert len(captured) == 1
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in captured[0]


def test_groq_planner_failure_uses_safe_fallback(monkeypatch):
    import groq

    class UnavailableGroq:
        def __init__(self, **kwargs):
            raise OSError("Provider unavailable")

    monkeypatch.setattr(groq, "Groq", UnavailableGroq)
    plan = agent_planner.plan_request("Inspect PDB 1LF2", api_key="unavailable")
    assert plan["source"] == "deterministic_fallback"
    assert plan["steps"] == agent_planner.STEP_LIBRARY["inspection"]


def test_chat_work_intent_starts_with_server_enabled_agent(monkeypatch, tmp_path):
    from pandoc import agent_ui
    monkeypatch.setattr(agent_ui, "_task_root", lambda st: tmp_path)
    started = []
    monkeypatch.setattr(agent_ui.agent_planner, "plan_request",
                        lambda instruction, **kwargs: {"source": "deterministic", "goal": "redocking"})
    monkeypatch.setattr(agent_ui.task_agent, "create",
                        lambda root, instruction, plan: (
                            started.append((root, instruction, plan))
                            or {"id": "d" * 32, "stage": "await_structure_review",
                                "pdb_id": "1LF2"}
                        ))
    from types import SimpleNamespace
    st = SimpleNamespace(
        secrets={"PANDOC_AGENT_ENABLED": "true"},
        session_state={},
    )
    assert agent_ui.handle_chat_request(st, "HELLO") is None
    assert agent_ui.handle_chat_request(st, "How do I prepare PDB 1LF2?") is None
    assert not started
    text = agent_ui.handle_chat_request(
        st, "Prepare PDB 1LF2 at pH 5.0 and validate by redocking"
    )
    assert "Created task" in text
    assert "No calculation has been submitted" in text
    assert st.session_state["pandoc_agent_task_id"] == "d" * 32
    assert len(started) == 1
    assert started[0][0] == tmp_path


def test_chat_agent_does_not_execute_when_disabled(monkeypatch):
    from pandoc import agent_ui
    from types import SimpleNamespace
    st = SimpleNamespace(
        secrets={"PANDOC_AGENT_ENABLED": "false",
                 "PANDOC_AGENT_ACCESS_KEY": "irrelevant"},
        session_state={},
    )
    monkeypatch.setattr(agent_ui.task_agent, "create",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("Disabled agent must not run")))
    answer = agent_ui.handle_chat_request(st, "Prepare PDB 1LF2 at pH 5.0")
    assert "not enabled" in answer
