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
        "--global.developmentMode=false",
        "--theme.base=light",
        "--theme.primaryColor=#0b6672",
        "--theme.backgroundColor=#e9eff6",
        "--theme.secondaryBackgroundColor=#f5f8fc",
        "--theme.textColor=#16293f",
        "--server.port=" + port,
        "--server.headless=true",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=true",
        "--browser.gatherUsageStats=false",
    ]
    streamlit_cli.main()


def run_selftest() -> None:
    """Import the actual application dependency graph inside the frozen EXE."""
    import traceback
    log_path = Path(os.environ.get("PANDOC_SELFTEST_LOG", str(Path.home() / "PanDocData" / "desktop-selftest.log")))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        try:
            print("Checking bundled PanDoc modules", file=log, flush=True)
            from cryptography.fernet import Fernet
            key = Fernet.generate_key()
            assert Fernet(key).decrypt(Fernet(key).encrypt(b"pandoc-check")) == b"pandoc-check"
            print("cryptography Fernet round-trip: OK", file=log, flush=True)
            import importlib.util
            assert importlib.util.find_spec("propka.__main__") is not None, "PROPKA CLI missing"
            print("PROPKA command entrypoint: OK", file=log, flush=True)
            from pandoc import (core, jobs, figures, pdb_search, structure_checks,
                                phprep, ui, batch_review, profile_engine,
                                profile_quality, compute, compute_options,
                                structure_view, viewer_runtime, pubchem_search)
            from pandoc.computer_d_compute import ComputerDCompute
            from pandoc.computer_e_compute import ComputerECompute
            print("PanDoc application imports: OK", file=log, flush=True)
        except BaseException:
            traceback.print_exc(file=log)
            raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--pandoc-selftest":
        run_selftest()
        return
    # Meeko receptor preparation invokes sys.executable -m ... .
    # In a frozen app sys.executable points to PanDoc.exe, not python.exe.
    if getattr(sys, "frozen", False) and len(sys.argv) > 2 and sys.argv[1] == "-m":
        import runpy
        module = sys.argv[2]
        if module not in {"meeko.cli.mk_prepare_receptor", "propka"}:
            raise SystemExit("Unsupported bundled module invocation")
        # A GUI/windowed bootloader has no stdout/stderr, which command-line
        # scientific packages expect. Preserve their diagnostic output.
        cli_log = Path.cwd() / ("propka_cli.log" if module == "propka" else "meeko_cli.log")
        with cli_log.open("w", encoding="utf-8", buffering=1) as output:
            sys.stdout = output
            sys.stderr = output
            sys.argv = [module, *sys.argv[3:]]
            try:
                runpy.run_module(module, run_name="__main__", alter_sys=True)
            except BaseException:
                import traceback
                traceback.print_exc(file=output)
                raise
        return
    # The frozen executable runs itself with a private server flag.
    if len(sys.argv) > 1 and sys.argv[1] == "--pandoc-server":
        # --windowed executables have no console streams. Log startup exceptions
        # explicitly so CI and installed desktops can diagnose failures.
        import traceback
        log_path = Path(os.environ.get("PANDOC_DESKTOP_LOG", str(Path.home() / "PanDocData" / "desktop-server.log")))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", buffering=1) as logfile:
            sys.stdout = logfile
            sys.stderr = logfile
            try:
                print(f"PanDoc server starting: {sys.executable}", flush=True)
                run_server()
            except BaseException:
                traceback.print_exc(file=logfile)
                raise
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
    env.setdefault("PANDOC_DESKTOP_LOG", str(Path.home() / "PanDocData" / "desktop-server.log"))
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
