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
    assert not list(at.exception)
    assert not calls
    assert not any("access key" in x.label.lower() for x in at.text_input)
    assert "Unlock agent" not in [button.label for button in at.button]
    assert any("Start task" in button.label for button in at.button)
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



def test_task_chat_accepts_followup_and_remembers_answer(monkeypatch, tmp_path):
    at, calls, task_id = _panel(monkeypatch, tmp_path)
    _widget(at.text_area, "pandoc_agent_instruction").set_value("Inspect PDB 1LF2.").run()
    next(b for b in at.button if "Start task" in b.label).click().run()
    assert not list(at.exception)
    assert any("Send follow-up" in b.label for b in at.button)

    answers = []
    def fake_followup(root, ident, question, **kwargs):
        assert ident == task_id
        answers.append(question)
        (root / "agent_tasks" / ident / "conversation.json").write_text(json.dumps(
            [{"question": question, "answer": "I found R37 and SO4 in the deposited structure."}]
        ))
        return "I found R37 and SO4 in the deposited structure."
    monkeypatch.setattr(agent_ui.agent_conversation, "respond", fake_followup)

    _widget(at.text_input, "pandoc_agent_followup").set_value(
        "Which ligands are present?"
    ).run()
    next(b for b in at.button if "Send follow-up" in b.label).click().run()
    assert not list(at.exception)
    assert answers == ["Which ligands are present?"]
    assert any("R37 and SO4" in m.value for m in at.markdown)
    at.run()
    assert len(answers) == 1  # reruns do not repeat actions


def test_existing_scientific_chat_routes_followups_to_active_task(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from pandoc import task_agent
    task_id = "b" * 32
    monkeypatch.setattr(agent_ui, "_task_root", lambda st: tmp_path)
    monkeypatch.setattr(task_agent, "describe", lambda root, ident: {
        "id": task_id, "pdb_id": "1LF2",
        "goal": "inspection", "stage": "await_structure_review"
    })
    calls = []
    monkeypatch.setattr(agent_ui.agent_conversation, "respond",
                        lambda root, ident, message, **kwargs: (
                            calls.append((ident, message)) or "The observed ligand was R37."
                        ))
    st = SimpleNamespace(
        secrets={"PANDOC_AGENT_ENABLED": "true"},
        session_state={"pandoc_agent_task_id": task_id},
    )
    assert agent_ui.handle_chat_request(st, "What did you find?") == "The observed ligand was R37."
    assert calls == [(task_id, "What did you find?")]
    assert agent_ui.handle_chat_request(st, "Hello") is None
    assert agent_ui.handle_chat_request(st, "What is PanDoc?") is None
    assert "different PDB" in agent_ui.handle_chat_request(st, "Prepare PDB 2XYZ")
    assert len(calls) == 1



def test_no_frontend_key_or_unlock_button_even_when_legacy_secret_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_ui, "_secret", lambda st, name: {
        "PANDOC_AGENT_ENABLED": "true",
        "PANDOC_AGENT_ACCESS_KEY": "legacy-unused-secret",
        "GROQ_API_KEY": "backend-only",
    }.get(name, ""))
    monkeypatch.setattr(agent_ui, "_task_root", lambda st: tmp_path)
    script = (
        "import streamlit as st\n"
        "from pandoc import agent_ui\n"
        "agent_ui.render(st, " + repr(str(tmp_path)) + ", lambda: None)\n"
    )
    at = AppTest.from_string(script).run()
    assert not list(at.exception)
    assert not any("key" in inp.label.lower() for inp in at.text_input)
    assert not any("Unlock" in btn.label or "Lock agent" in btn.label for btn in at.button)
    assert any("Start task" in btn.label for btn in at.button)
    assert any("Agent ready" in item.value for item in at.info)
    assert "PANDOC_AGENT_ACCESS_KEY" not in "".join(m.value for m in at.markdown)


def test_agent_remains_deployment_controlled_not_password_controlled(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_ui, "_secret", lambda st, name: {
        "PANDOC_AGENT_ENABLED": "false",
        "PANDOC_AGENT_ACCESS_KEY": "ignored",
    }.get(name, ""))
    script = (
        "import streamlit as st\n"
        "from pandoc import agent_ui\n"
        "agent_ui.render(st, " + repr(str(tmp_path)) + ", lambda: None)\n"
    )
    at = AppTest.from_string(script).run()
    assert not list(at.exception)
    assert not [btn for btn in at.button if "Start task" in btn.label]
