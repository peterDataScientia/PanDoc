"""Background workers survive Streamlit reruns; each job has isolated outputs."""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from .core import validate_config


def launch(root, config):
    validate_config(config)
    directory = Path(root) / 'jobs' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    (directory/'config.json').write_text(json.dumps(config, indent=2))
    (directory/'status.json').write_text(json.dumps({'state': 'queued', 'completed': 0}))
    with (directory/'worker.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-u', '-m', 'pandoc.worker', str(directory)], stdout=log, stderr=log, start_new_session=True)
    (directory/'worker.pid').write_text(str(process.pid))
    return str(directory)


def _worker_alive(pid):
    """A Linux zombie is not a live worker, even though os.kill(pid, 0) succeeds."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass

    proc_root = Path('/proc')
    if proc_root.is_dir():
        try:
            # The command name in /proc/PID/stat can contain spaces and ')'.
            state = (proc_root / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            return False
        except (OSError, IndexError):
            return True
        if state in ('Z', 'X', 'x'):
            return False
    return True


def status(directory):
    root = Path(directory)
    try:
        data = json.loads((root/'status.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {'state': 'starting', 'completed': 0}

    pid_file = root / 'worker.pid'
    if data.get('state') in ('running', 'queued') and pid_file.exists():
        try:
            alive = _worker_alive(int(pid_file.read_text()))
        except (OSError, ValueError):
            alive = False
        if not alive:
            last_stage = data.get('stage') or data.get('current') or 'unknown'
            data.update(
                state='failed',
                stage='failed',
                error=(
                    'Worker exited unexpectedly during ' + str(last_stage) +
                    '. The operating system may have terminated the process, or a '
                    'native docking library may have crashed. Inspect worker.log.'
                ),
            )
            # Persist the failure so repeated Streamlit reruns show the same state.
            tmp = root / 'status_monitor.tmp'
            tmp.write_text(json.dumps(data))
            tmp.replace(root / 'status.json')
    return data


def cancel(directory):
    (Path(directory)/'cancel.request').touch()


def list_jobs(root):
    return sorted((Path(root)/'jobs').glob('*'), key=lambda p: p.stat().st_mtime, reverse=True)
