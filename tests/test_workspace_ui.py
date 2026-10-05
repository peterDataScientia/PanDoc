from pathlib import Path

from pandoc import workspace_ui


class EmptyJobs:
    @staticmethod
    def list_jobs(root):
        return []


def test_workspace_stage_round_trip():
    for view, stage, _ in workspace_ui.WORKSPACES:
        assert workspace_ui.stage_for_view(view) == stage
        assert workspace_ui.view_from_stage(stage) == view


def test_recommendation_moves_with_project_state(tmp_path: Path):
    state = {}
    snap = workspace_ui.project_snapshot(state, tmp_path, EmptyJobs)
    assert workspace_ui.recommended_view(snap) == "Structure"

    state["pdb"] = "ATOM"
    snap = workspace_ui.project_snapshot(state, tmp_path, EmptyJobs)
    assert workspace_ui.recommended_view(snap) == "Preparation"

    state["receptor_path"] = "receptor.pdbqt"
    state["reference_path"] = "reference.sdf"
    snap = workspace_ui.project_snapshot(state, tmp_path, EmptyJobs)
    assert workspace_ui.recommended_view(snap) == "Validation"


def test_candidate_count_is_project_state(tmp_path: Path):
    state = {"candidate_paths": [{"id": "a"}, {"id": "b"}]}
    snap = workspace_ui.project_snapshot(state, tmp_path, EmptyJobs)
    assert snap["candidate_count"] == 2
