"""PanDoc Windows desktop launcher. Keep the hosted Streamlit app unchanged."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def resource_path(relative: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / relative
    return Path(__file__).resolve().parents[1] / relative


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_server() -> None:
    from streamlit.web import cli as streamlit_cli

    app = resource_path("app.py")
    if getattr(sys, "frozen", False):
        vina = resource_path("bin/vina.exe")
        if not vina.is_file():
            raise RuntimeError(f"Bundled Vina executable missing: {vina}")
        os.environ["PANDOC_VINA_EXE"] = str(vina)
    port = os.environ["PANDOC_DESKTOP_PORT"]
    sys.argv = [
        "streamlit", "run", str(app),
        "--server.address=127.0.0.1",
        "--server.port=" + port,
        "--server.headless=true",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=true",
        "--browser.gatherUsageStats=false",
    ]
    streamlit_cli.main()


def main() -> None:
    # Meeko receptor preparation invokes sys.executable -m ... .
    # In a frozen app sys.executable points to PanDoc.exe, not python.exe.
    if getattr(sys, "frozen", False) and len(sys.argv) > 2 and sys.argv[1] == "-m":
        import runpy
        module = sys.argv[2]
        if module not in {"meeko.cli.mk_prepare_receptor"}:
            raise SystemExit("Unsupported bundled module invocation")
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return
    # The frozen executable runs itself with a private server flag.
    if len(sys.argv) > 1 and sys.argv[1] == "--pandoc-server":
        run_server()
        return

    try:
        import webview
    except ImportError as exc:
        raise SystemExit("Missing pywebview desktop dependency") from exc

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PANDOC_DESKTOP_PORT"] = str(port)
    env.setdefault("PANDOC_DATA_DIR", str(Path.home() / "PanDocData"))
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--pandoc-server"]
    else:
        args = [sys.executable, str(Path(__file__).resolve()), "--pandoc-server"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    child = subprocess.Popen(args, env=env, creationflags=flags)
    try:
        for _ in range(120):
            if child.poll() is not None:
                raise RuntimeError("PanDoc server exited before startup")
            try:
                with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise TimeoutError("PanDoc local server did not start")
        webview.create_window("PanDoc | Docking Workbench", url, width=1350, height=850, min_size=(900, 600))
        webview.start(gui="edgechromium")
    finally:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()


if __name__ == "__main__":
    main()
