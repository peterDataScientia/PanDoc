"""Computer D emits measured progress while Vina is still running."""
import sys
from pathlib import Path

from computer_d_worker import run_vina_live


def test_streaming_output_and_periodic_heartbeat(tmp_path):
    updates = []
    program = (
        "import time; "
        "print('Starting docking', flush=True); "
        "time.sleep(0.4); "
        "print('Search finished', flush=True)"
    )
    code, output = run_vina_live(
        [sys.executable, "-u", "-c", program],
        Path(tmp_path),
        lambda lines, elapsed, changed: updates.append(
            (lines, elapsed, changed)
        ),
        timeout_seconds=5,
        publish_interval=0.1,
    )
    assert code == 0
    assert "Starting docking" in output
    assert "Search finished" in output
    assert len(updates) >= 2
    assert any("Starting docking" in lines for lines, _, _ in updates)
    assert all(elapsed >= 0 for _, elapsed, _ in updates)
