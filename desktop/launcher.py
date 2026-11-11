"""PanDockLab Windows desktop launcher (legacy PanDoc.exe module/paths)."""
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
    # Mark only this frozen local server as eligible for desktop updates.
    # Keep the hosted Streamlit app and remote compute nodes unaffected.
    version_file = resource_path("desktop/VERSION")
    if getattr(sys, "frozen", False) and not version_file.is_file():
        raise RuntimeError("Bundled desktop version metadata missing.")
    os.environ["PANDOC_DESKTOP_APP"] = "1" if getattr(sys, "frozen", False) else "0"
    os.environ["PANDOC_DESKTOP_VERSION"] = (
        version_file.read_text(encoding="utf-8").strip() if version_file.is_file()
        else "0.3.3"
    )
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
            print("Checking bundled PanDockLab modules", file=log, flush=True)
            from pandoc.desktop_updater import version_tuple
            assert version_tuple("0.3.0") == (0, 3, 0)
            print("PanDockLab in-app updater import: OK", file=log, flush=True)
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
            print("PanDockLab application imports: OK", file=log, flush=True)
        except BaseException:
            traceback.print_exc(file=log)
            raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--pandoc-selftest":
        run_selftest()
        return
    # Reproduce the supported Python -m invocations used by PanDoc.
    # PyInstaller replaces sys.executable with PanDoc.exe in frozen apps.
    if getattr(sys, "frozen", False) and len(sys.argv) > 2:
        args = sys.argv[1:]
        if args and args[0] == "-u":
            args = args[1:]
        if len(args) >= 2 and args[0] == "-m":
            import runpy
            module = args[1]
            if module not in {"meeko.cli.mk_prepare_receptor", "propka", "pandoc.worker"}:
                raise SystemExit("Unsupported bundled module invocation")
            sys.argv = [module, *args[2:]]
            if module == "pandoc.worker":
                # In --windowed builds Python streams are None despite the
                # parent's OS-level redirection. Explicitly append to the same
                # job logfile so the Streamlit live-log panel keeps working.
                if len(args) < 3:
                    raise SystemExit("Missing docking worker job directory")
                worker_log = Path(args[2]) / "worker.log"
                old_stdout, old_stderr = sys.stdout, sys.stderr
                try:
                    with worker_log.open("a", encoding="utf-8", buffering=1) as output:
                        sys.stdout = output
                        sys.stderr = output
                        runpy.run_module(module, run_name="__main__", alter_sys=True)
                finally:
                    sys.stdout, sys.stderr = old_stdout, old_stderr
                return
            # PROPKA has an explicit writable working directory. Meeko does
            # not set cwd, so put its diagnostic log beside its output files,
            # not under the application's read-only installation directory.
            if module == "propka":
                cli_log = Path.cwd() / "propka_cli.log"
            else:
                output_dir = None
                if "-o" in sys.argv:
                    index = sys.argv.index("-o")
                    if index + 1 < len(sys.argv):
                        output_dir = Path(sys.argv[index + 1]).parent
                cli_log = (output_dir or Path(os.environ.get("PANDOC_DATA_DIR", str(Path.home() / "PanDocData")))) / "meeko_cli.log"
            cli_log.parent.mkdir(parents=True, exist_ok=True)
            old_stdout, old_stderr = sys.stdout, sys.stderr
            try:
                with cli_log.open("w", encoding="utf-8", buffering=1) as output:
                    sys.stdout = output
                    sys.stderr = output
                    try:
                        runpy.run_module(module, run_name="__main__", alter_sys=True)
                    except BaseException:
                        import traceback
                        traceback.print_exc(file=output)
                        raise
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
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
                print(f"PanDockLab server starting: {sys.executable}", flush=True)
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
                raise RuntimeError("PanDockLab server exited before startup")
            try:
                with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise TimeoutError("PanDockLab local server did not start")
        webview.create_window("PanDockLab | Docking Workbench", url, width=1350, height=850, min_size=(900, 600))
        webview.start(gui="edgechromium")
    finally:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()


if __name__ == "__main__":
    main()
