from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .github_action_worker import _run_candidate_preparation, _run_ligand_microstates


def main(directory: str, operation: str) -> None:
    result_dir = Path(directory)
    try:
        if operation == "ligand-microstates":
            print("Starting ligand-state enumeration on Computer B", flush=True)
            _run_ligand_microstates(result_dir, result_dir)
        elif operation == "prepare-candidates":
            print("Starting candidate preparation on Computer B", flush=True)
            _run_candidate_preparation(result_dir, result_dir)
        else:
            raise RuntimeError(f"Unsupported Computer B operation: {operation}")
        print(f"Computer B {operation} completed.", flush=True)
    except Exception as exc:
        traceback.print_exc()
        (result_dir / "status.json").write_text(
            json.dumps({"state": "failed", "completed": 0, "total": 1, "error": str(exc)}, indent=2)
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python -m pandoc.kaggle_task_worker DIRECTORY OPERATION")
    main(sys.argv[1], sys.argv[2])
