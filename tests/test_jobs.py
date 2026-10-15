"""Regression checks for local docking-worker failure reporting."""
import json

from pandoc import jobs, worker


def _job(tmp_path, state="running", stage="writing_poses"):
    (tmp_path / "status.json").write_text(json.dumps({
        "state": state, "stage": stage, "completed": 0, "total": 1,
    }))
    (tmp_path / "worker.pid").write_text("987654")
    return tmp_path


def test_dead_worker_fails_instead_of_staying_running(tmp_path, monkeypatch):
    job = _job(tmp_path)
    monkeypatch.setattr(jobs, "_worker_alive", lambda pid: False)
    result = jobs.status(job)
    assert result["state"] == "failed"
    assert result["stage"] == "failed"
    assert "writing_poses" in result["error"]
    assert json.loads((job / "status.json").read_text()) == result
    # The failure remains visible after the next Streamlit rerun.
    assert jobs.status(job)["state"] == "failed"


def test_alive_worker_keeps_current_stage(tmp_path, monkeypatch):
    job = _job(tmp_path, stage="computing_rmsd")
    monkeypatch.setattr(jobs, "_worker_alive", lambda pid: True)
    result = jobs.status(job)
    assert result["state"] == "running"
    assert result["stage"] == "computing_rmsd"


def test_completed_job_not_mislabeled_after_worker_exit(tmp_path, monkeypatch):
    job = _job(tmp_path, state="completed", stage="completed")
    monkeypatch.setattr(
        jobs, "_worker_alive",
        lambda pid: (_ for _ in ()).throw(AssertionError("should not poll completed PID")),
    )
    assert jobs.status(job)["state"] == "completed"


def test_preflight_failure_is_written_to_status(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    worker.run(tmp_path)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "failed"
    assert status["stage"] == "failed"
    assert status["error"]
