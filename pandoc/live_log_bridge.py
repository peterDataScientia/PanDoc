"""Publish encrypted, near-live Computer C docking output to its temporary job branch.

GitHub's job-log download API may return 404 until the job finishes. This
bridge runs OUTSIDE the Docker container in the Actions runner, tails the
container's actual stdout (including native AutoDock Vina output), encrypts it
with the existing PanDoc Fernet key, and updates one branch-scoped blob.

Do not publish plaintext scientific inputs or logs in repository contents.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet

LOG_CHARS = 64000
DEFAULT_INTERVAL_SECONDS = 12
_STOP = threading.Event()


def _stop(*_args):
    _STOP.set()


def _github_request(token, method, url, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def publish_snapshot(*, job_id, branch, repository, log_path, token, cipher, last_text):
    """Upload only changed log content; return snapshot text after publication."""
    path = Path(log_path)
    if not path.exists():
        return last_text
    # Read as bytes to avoid UTF-8 boundary errors when truncating live output.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 4 * LOG_CHARS))
        content = stream.read()
    text = content.decode("utf-8", errors="replace")[-LOG_CHARS:]
    if not text.strip() or text == last_text:
        return last_text

    relative = f".pandoc_jobs/live/{job_id}.bin"
    target = f"https://api.github.com/repos/{repository}/contents/{relative}"
    sha = None
    try:
        current = _github_request(token, "GET", target + "?ref=" + urllib.parse.quote(branch, safe=""))
        sha = current.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    encrypted = cipher.encrypt(text.encode("utf-8"))
    request_body = {
        "branch": branch,
        "message": f"Update encrypted PanDoc docking output {job_id[:8]} [skip ci]",
        "content": base64.b64encode(encrypted).decode("ascii"),
    }
    if sha:
        request_body["sha"] = sha
    _github_request(token, "PUT", target, request_body)
    return text


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    # Fail closed: no public plaintext fallback if encryption/token is missing.
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    key = os.environ.get("PANDOC_JOB_KEY", "").strip()
    if not token or not key:
        raise SystemExit("Encrypted live log bridge needs GITHUB_TOKEN and PANDOC_JOB_KEY.")
    cipher = Fernet(key.encode("utf-8"))
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_text = ""
    while not _STOP.is_set():
        try:
            last_text = publish_snapshot(
                job_id=args.job_id,
                branch=args.branch,
                repository=args.repository,
                log_path=args.log_path,
                token=token,
                cipher=cipher,
                last_text=last_text,
            )
        except (OSError, ValueError) as exc:
            # Upload failures must never terminate the scientific calculation.
            print(f"PanDoc live log delivery retry: {type(exc).__name__}", flush=True)
        _STOP.wait(max(2.0, args.interval))
    # A final flush includes the last energy scores or exceptions.
    try:
        publish_snapshot(
            job_id=args.job_id,
            branch=args.branch,
            repository=args.repository,
            log_path=args.log_path,
            token=token,
            cipher=cipher,
            last_text=last_text,
        )
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    main()
