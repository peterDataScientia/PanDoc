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
        process = subprocess.Popen([sys.executable, '-m', 'pandoc.worker', str(directory)], stdout=log, stderr=log, start_new_session=True)
    (directory/'worker.pid').write_text(str(process.pid))
    return str(directory)


def status(directory):
    root = Path(directory)
    try:
        data = json.loads((root/'status.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {'state': 'starting', 'completed': 0}
    if data['state'] in ('running', 'queued') and (root/'worker.pid').exists():
        try:
            os.kill(int((root/'worker.pid').read_text()), 0)
        except ProcessLookupError:
            data = {'state': 'failed', 'error': 'Worker exited before completion. Inspect worker.log.', 'completed': data.get('completed', 0)}
    return data


def cancel(directory):
    (Path(directory)/'cancel.request').touch()


def list_jobs(root):
    return sorted((Path(root)/'jobs').glob('*'), key=lambda p: p.stat().st_mtime, reverse=True)
