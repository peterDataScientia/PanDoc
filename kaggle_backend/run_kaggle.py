from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from kaggle_secrets import UserSecretsClient
        return (UserSecretsClient().get_secret(name) or "").strip()
    except Exception:
        return ""


def cloudflared_binary() -> str:
    existing = shutil.which("cloudflared")
    if existing:
        return existing
    machine = platform.machine().lower()
    arch = "amd64" if machine in {"x86_64", "amd64"} else "arm64"
    path = Path(tempfile.gettempdir()) / "cloudflared"
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
    print("Downloading cloudflared…", flush=True)
    urllib.request.urlretrieve(url, path)
    path.chmod(0o755)
    return str(path)


def main():
    api_key = secret("PANDOC_KAGGLE_API_KEY")
    if not api_key:
        raise SystemExit("Add a Kaggle secret named PANDOC_KAGGLE_API_KEY before starting the backend.")
    os.environ["PANDOC_KAGGLE_API_KEY"] = api_key

    host = "127.0.0.1"
    port = int(os.environ.get("PANDOC_KAGGLE_PORT", "8000"))
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kaggle_backend.server:app", "--host", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(2)
    if server.poll() is not None:
        print(server.stdout.read(), flush=True)
        raise SystemExit("PanDoc backend failed to start.")

    tunnel = subprocess.Popen(
        [cloudflared_binary(), "tunnel", "--url", f"http://{host}:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    endpoint = None
    deadline = time.time() + 60
    while time.time() < deadline:
        line = tunnel.stdout.readline()
        if not line:
            if tunnel.poll() is not None:
                break
            continue
        print(line.rstrip(), flush=True)
        match = pattern.search(line)
        if match:
            endpoint = match.group(0)
            break
    if not endpoint:
        tunnel.terminate()
        server.terminate()
        raise SystemExit("Cloudflare Quick Tunnel did not return an endpoint.")

    print("\nPAN-DOC KAGGLE DIRECT BACKEND READY", flush=True)
    print(endpoint, flush=True)
    print("Put this URL in Streamlit secret PANDOC_KAGGLE_URL.", flush=True)
    print("Keep this Kaggle session running. The URL changes whenever the session restarts.\n", flush=True)

    try:
        while server.poll() is None and tunnel.poll() is None:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        server.terminate()


if __name__ == "__main__":
    main()
