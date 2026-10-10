"""Scientific evidence tool contracts: correctness, minimization and consent."""
import json
from pathlib import Path
from types import SimpleNamespace

from pandoc import assistant, assistant_tools


def _job(tmp_path, rows):
    (tmp_path / "results.json").write_text(json.dumps(rows))
    (tmp_path / "config.json").write_text(json.dumps({
        "receptor": "/secret/private.pdbqt", "center": [1, 2, 3],
        "size": [20, 20, 20], "seeds": [1, 2], "exhaustiveness": 16,
        "poses": 9, "cpu": 2,
    }))
    (tmp_path / "status.json").write_text(json.dumps({
        "state": "completed", "completed": 2, "total": 2,
        "error": "Do not leak this private exception",
    }))
    return {"job": tmp_path}


def test_summary_analyzes_all_saved_rows_not_first_fifty(tmp_path):
    rows = [
        {"ligand_id": "secret-L1", "ligand": "private-name", "seed": 1,
         "rank": i + 1, "score_kcal_mol": -6 - i / 100,
         "reference_rmsd_A": 5.0} for i in range(60)
    ]
    rows += [
        {"ligand_id": "secret-L1", "seed": 2, "rank": 1,
         "score_kcal_mol": -8.3, "reference_rmsd_A": 0.9},
        {"ligand_id": "private-L2", "seed": 1, "rank": 1,
         "score_kcal_mol": -4.0},
    ]
    evidence = _job(tmp_path, rows)
    info = assistant_tools.dispatch("get_calculation_summary", {}, evidence)
    assert info["saved_pose_count"] == 62
    assert info["analyzed_all_saved_rows"]
    a, b = info["compounds"]
    assert a["compound"] == "Compound 1"
    assert b["compound"] == "Compound 2"
    assert a["best_reference_rmsd_A"] == 0.9
    assert a["reference_seeds_assessed"] == 2
    assert a["best_pose_recovery_le_2A_seeds"] == 1
    assert a["top_rank_recovery_le_2A_seeds"] == 1
    assert b["reference_seeds_assessed"] == 0
    assert "secret" not in json.dumps(info)
    assert "private" not in json.dumps(info)


def test_rows_are_paginated_anonymized_and_sorted(tmp_path):
    evidence = _job(tmp_path, [
        {"ligand_id": "hidden", "ligand": "Do not share", "sdf": "private.sdf",
         "seed": 1, "rank": 1, "score_kcal_mol": -5},
        {"ligand_id": "other", "seed": 2, "rank": 1,
         "score_kcal_mol": -10, "reference_rmsd_A": 1.3},
    ])
    data = assistant_tools.dispatch(
        "get_calculation_rows", {"sort_by": "score", "limit": 1}, evidence)
    assert data["total_rows"] == 2
    assert data["rows"][0]["compound"] == "Compound 2"
    assert data["rows"][0]["score_kcal_mol"] == -10
    assert data["rows"][0]["row_index"] == 1
    assert "hidden" not in json.dumps(data)
    assert "private.sdf" not in json.dumps(data)
    assert "Do not share" not in json.dumps(data)
    assert assistant_tools.dispatch(
        "get_calculation_rows", {"limit": 100}, evidence)["error"]
    assert assistant_tools.dispatch(
        "get_calculation_rows", {"offset": -3}, evidence)["error"]


def test_only_preselected_job_and_known_tools_may_be_used(tmp_path):
    evidence = _job(tmp_path, [{"ligand_id": "safe", "score_kcal_mol": -5}])
    assert "error" in assistant_tools.dispatch("delete_file", {}, evidence)
    assert assistant_tools.dispatch(
        "get_calculation_rows", {"path": "/etc/passwd"}, evidence)["rows"][0]["compound"] == "Compound 1"
    provenance = assistant_tools.dispatch("get_job_provenance", {}, evidence)
    assert provenance["job_status"]["state"] == "completed"
    assert provenance["calculation_settings"]["exhaustiveness"] == 16
    assert "private.pdbqt" not in json.dumps(provenance)
    assert "Do not leak" not in json.dumps(provenance)


def test_structure_residue_inventory_and_recorded_issues():
    fixture = (Path(__file__).parent / "browser" / "fixture_complex.pdb").read_text()
    evidence = {
        "pdb": fixture,
        "checks": {"atom_count": 8, "issues": [
            {"severity": "Error", "residue": "A:1:ALA", "problem": "Severe overlap",
             "action": "Inspect structure"}]},
        "report": {"repair_changes": [
            {"residue": "A:1:ALA", "atom": "CB", "change": "Moved heavy atom"}]},
    }
    residue = assistant_tools.dispatch("inspect_residue", {"residue": "A:1:ALA"}, evidence)
    assert residue["available"]
    assert residue["heavy_atom_count"] == 5
    assert not any("xyz" in atom or "line" in atom for atom in residue["atoms"])
    assert "5.000" not in json.dumps(residue)
    assert not assistant_tools.dispatch(
        "inspect_residue", {"residue": "X:123:AAA"}, evidence)["available"]
    issues = assistant_tools.dispatch("get_structure_evidence", {}, evidence)
    assert issues["issue_count"] == 1
    assert issues["recorded_change_count"] == 1


def test_groq_local_tool_round_trip_and_no_unconsented_tools(monkeypatch, tmp_path):
    import groq
    evidence = _job(tmp_path, [{"ligand_id": "private", "seed": 1,
                                "rank": 1, "score_kcal_mol": -8.4}])
    queries = []

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            queries.append(kwargs)
            if len(queries) == 1:
                call = SimpleNamespace(
                    id="call_1", function=SimpleNamespace(
                        name="get_calculation_summary", arguments="{}"))
                msg = SimpleNamespace(content=None, tool_calls=[call])
            else:
                msg = SimpleNamespace(content="One pose was recorded.", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    result = assistant.ask("How many saved poses?", "fake-key", {}, evidence=evidence)
    assert result == "One pose was recorded."
    assert queries[0]["tool_choice"] == "auto"
    assert queries[1]["messages"][-1]["role"] == "tool"
    assert json.loads(queries[1]["messages"][-1]["content"])["saved_pose_count"] == 1
    assert "private" not in queries[1]["messages"][-1]["content"]

    queries.clear()
    assistant.ask("General scientific question", "fake-key", context=None, evidence=evidence)
    assert "tools" not in queries[0]


def test_malformed_model_tool_arguments_do_not_execute(monkeypatch):
    import groq
    calls = []

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                msg = SimpleNamespace(content=None, tool_calls=[
                    SimpleNamespace(id="call_bad", function=SimpleNamespace(
                        name="get_calculation_rows", arguments="{invalid json"))
                ])
            else:
                msg = SimpleNamespace(content="Invalid arguments were rejected.",
                                      tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(groq, "Groq", FakeGroq)
    assert assistant.ask("Inspect records", "fake", {}, evidence={}) == "Invalid arguments were rejected."
    assert "error" in json.loads(calls[1]["messages"][-1]["content"])
