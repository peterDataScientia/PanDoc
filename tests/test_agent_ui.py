"""Regression tests: a task instruction must never be treated as a task ID."""
import json

from streamlit.testing.v1 import AppTest

from pandoc import agent_ui


def _widget(items, key):
    return next(item for item in items if item.key == key)


def _panel(monkeypatch, tmp_path):
    calls = []
    task_id = "d" * 32
    root = tmp_path

    def fake_secret(_st, name):
        return {
            "PANDOC_AGENT_ENABLED": "true",
            "PANDOC_AGENT_ACCESS_KEY": "pilot-password",
            "GROQ_API_KEY": "",
            "GROQ_MODEL": "",
        }.get(name, "")

    def fake_create(folder, instruction, plan=None):
        calls.append((folder, instruction, plan))
        dest = root / "agent_tasks" / task_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "inspection.json").write_text(json.dumps({
            "components": [
                {"residue": "A:1:ALA", "name": "ALA", "kind": "Protein"},
                {"residue": "A:101:EOH", "name": "EOH", "kind": "Other component"},
            ],
            "alternates": [],
        }))
        return {"id": task_id, "stage": "await_structure_review", "pdb_id": "1LF2"}

    def fake_describe(folder, ident):
        assert ident == task_id, "No natural-language instruction may reach task lookup"
        return {
            "id": task_id,
            "stage": "await_structure_review",
            "goal": "inspection",
            "pdb_id": "1LF2",
            "pH": None,
            "receptor_chains": ["A"],
            "structural_issue_count": 0,
            "structural_issues": [],
            "reference_candidates": [{"residue": "A:101:EOH", "component": "EOH", "heavy_atoms": 3}],
        }

    monkeypatch.setattr(agent_ui, "_secret", fake_secret)
    monkeypatch.setattr(agent_ui, "_task_root", lambda st: root)
    monkeypatch.setattr(agent_ui.agent_planner, "plan_request",
                        lambda instruction, **kwargs: {"summary": "Inspect PDB 1LF2",
                                                      "scientific_caution": "No docking requested."})
    monkeypatch.setattr(agent_ui.task_agent, "create", fake_create)
    monkeypatch.setattr(agent_ui.task_agent, "describe", fake_describe)

    script = (
        "import streamlit as st\n"
        "from pandoc import agent_ui\n"
        "agent_ui.render(st, " + repr(str(root)) + ", lambda: None)\n"
    )
    at = AppTest.from_string(script).run()
    assert not list(at.exception)
    _widget(at.text_input, "pandoc_agent_access_entry").set_value("pilot-password").run()
    assert not list(at.exception)
    return at, calls, task_id


def test_natural_language_instruction_submits_and_generates_separate_id(monkeypatch, tmp_path):
    at, calls, task_id = _panel(monkeypatch, tmp_path)
    area = _widget(at.text_area, "pandoc_agent_instruction")
    area.set_value("Inspect PDB 1LF2.").run()
    assert not calls  # Typing does not submit or look up the instruction.
    assert "Unknown agent task." not in " ".join(w.value for w in at.warning)
    start = next(b for b in at.button if "Start task" in b.label)
    start.click().run()
    assert not list(at.exception)
    assert len(calls) == 1
    assert calls[0][1] == "Inspect PDB 1LF2."
    assert at.session_state["pandoc_agent_task_id"] == task_id
    assert "Unknown agent task." not in " ".join(w.value for w in at.warning)
    assert any("Structure inspection is complete" in m.value for m in at.success)
    assert any("Task stage" in m.value for m in at.markdown)
    # Ordinary reruns must not spawn a duplicate task.
    at.run()
    assert len(calls) == 1


def test_resume_field_rejects_plain_english_without_lookup(monkeypatch, tmp_path):
    at, calls, _ = _panel(monkeypatch, tmp_path)
    _widget(at.text_input, "pandoc_agent_resume_candidate").set_value("Inspect PDB 1LF2.").run()
    next(b for b in at.button if b.label == "Resume existing task").click().run()
    assert not list(at.exception)
    assert not calls
    assert any("That is not a task ID" in m.value for m in at.warning)
    assert "pandoc_agent_task_id" not in at.session_state


def test_empty_panel_has_distinct_submit_and_resume_actions(monkeypatch, tmp_path):
    at, calls, _ = _panel(monkeypatch, tmp_path)
    assert not calls
    labels = [b.label for b in at.button]
    assert any("Start task" in label for label in labels)
    assert "Resume existing task" in labels
    assert not any("Unknown agent task" in m.value for m in at.warning)
    assert any("No task started yet" in m.value for m in at.info)
