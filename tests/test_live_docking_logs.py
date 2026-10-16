"""Computer C streams encrypted real docking output, never GitHub runner steps."""
import base64
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from cryptography.fernet import Fernet

from pandoc import github_compute, live_log_bridge


def test_live_bridge_encrypts_native_vina_output_on_temporary_branch(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    cipher = Fernet(key.encode())
    native_stdout = (
        "Computing Vina grid ... done.\n"
        "Starting docking: Reference ligand / seed 2026\n"
        "Performing docking (random seed: 2026) ... 100% ... done.\n"
        "Vina search returned; exporting docked PDBQT poses...\n"
        "=== Vina docking scores | Reference ligand | seed 2026 ===\n"
        "Mode    Affinity (kcal/mol)\n"
        "   1        -7.126\n"
        "Best affinity: -7.126 kcal/mol (mode 1)\n"
        "Writing SDF pose 1/2...\n"
    )
    source = tmp_path / "vina-live.log"
    source.write_text(native_stdout)
    requests = []

    def fake_request(token, method, url, payload=None):
        requests.append((method, url, payload))
        if method == "GET":
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return {"content": {"sha": "encrypted-blob"}}

    monkeypatch.setattr(live_log_bridge, "_github_request", fake_request)
    last = live_log_bridge.publish_snapshot(
        job_id="job123", branch="pandoc-job-job123",
        repository="peterDataScientia/PanDoc", log_path=source,
        token="secret-token", cipher=cipher, last_text="",
    )
    assert last == native_stdout
    assert [method for method, _, _ in requests] == ["GET", "PUT"]
    payload = requests[-1][2]
    assert payload["branch"] == "pandoc-job-job123"
    assert "[skip ci]" in payload["message"]
    assert native_stdout not in str(payload)
    assert b"Starting docking" not in base64.b64decode(payload["content"])
    assert cipher.decrypt(base64.b64decode(payload["content"])).decode() == native_stdout

    # An unchanged file must not create further commits.
    second = live_log_bridge.publish_snapshot(
        job_id="job123", branch="pandoc-job-job123",
        repository="peterDataScientia/PanDoc", log_path=source,
        token="secret-token", cipher=cipher, last_text=last,
    )
    assert second == last
    assert len(requests) == 2


def test_pan_doc_decodes_live_docking_log_with_real_vina_progress(monkeypatch):
    key = Fernet.generate_key().decode()
    client = github_compute.GitHubCompute(
        "token", key, "peterDataScientia/PanDoc"
    )
    raw = (
        "Computing Vina grid ... done.\n"
        "Performing docking ... 40%\rPerforming docking ... 100%\n"
        "Vina search returned; exporting docked PDBQT poses...\n"
        "Mode    Affinity (kcal/mol)\n"
        "   1        -7.126\n"
        "Best affinity: -7.126 kcal/mol (mode 1)\n"
        "Completed docking search 1/1\n"
    )
    contents = base64.b64encode(
        Fernet(key.encode()).encrypt(raw.encode())
    ).decode()

    def fake_request(_token, method, url, **kwargs):
        assert method == "GET"
        assert "actions/jobs" not in url
        assert "contents/.pandoc_jobs/live/job123.bin" in url
        assert kwargs["params"] == {"ref": "pandoc-job-job123"}
        return Mock(json=lambda: {"encoding": "base64", "content": contents})

    monkeypatch.setattr(github_compute, "_request", fake_request)
    output = client.logs({"job_id": "job123", "branch": "pandoc-job-job123"})
    assert "Performing docking ... 100%" in output
    assert "Best affinity: -7.126 kcal/mol (mode 1)" in output
    assert "Completed docking search 1/1" in output
    assert "\r" not in output
    assert "Log in to GitHub Container Registry" not in output


def test_missing_live_snapshot_never_shows_fake_workflow_progress(monkeypatch):
    key = Fernet.generate_key().decode()
    client = github_compute.GitHubCompute("token", key)

    def missing(*args, **kwargs):
        raise github_compute.GitHubComputeError("GitHub API 404: Not Found")

    monkeypatch.setattr(github_compute, "_request", missing)
    result = client.logs({"job_id": "job123", "branch": "pandoc-job-job123"})
    assert "Waiting for AutoDock Vina output" in result
    assert "Check out queued job" not in result


def test_docking_ui_does_not_present_runner_steps_as_progress():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    remote_ui = app.split("def show_remote_job(", 1)[1].split("def show_job(", 1)[0]
    assert "show_compute_progress(backend, remote)" not in remote_ui
    assert "AutoDock Vina · Live docking output" in remote_ui
    assert "run_every=refresh_seconds if polling else None" in remote_ui


def test_action_captures_container_stdout_including_native_vina():
    workflow = (Path(__file__).resolve().parents[1] /
                ".github" / "workflows" / "pandoc-compute.yml").read_text()
    assert "contents: write" in workflow
    assert "pandoc.live_log_bridge" in workflow
    assert "2>&1 | tee" in workflow
    assert "PYTHONUNBUFFERED=1" in workflow
