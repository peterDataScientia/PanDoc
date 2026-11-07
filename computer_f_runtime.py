"""Computer F: direct HTTPS streaming Vina runtime (no queue, no arbitrary code)."""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

app = FastAPI(title="PanDoc Computer F", docs_url=None, redoc_url=None)
MAX_BYTES = 25 * 1024 * 1024
CAPACITY = max(1, int(os.environ.get("PANDOC_F_MAX_CONCURRENT", "1")))
SLOTS = threading.BoundedSemaphore(CAPACITY)


def authorize(header: str | None):
    secret = os.environ.get("PANDOC_F_API_KEY", "")
    if not secret or not header or not hmac.compare_digest(header, secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health(x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    return {"runtime": "Computer F", "mode": "direct", "queue": False, "capacity": CAPACITY}


def event(kind, **kwargs):
    return json.dumps({"type": kind, **kwargs}, separators=(",", ":")) + "\n"


def execute(receptor: bytes, ligand: bytes, center, size, exhaustiveness, poses, seed):
    work = Path(tempfile.mkdtemp(prefix="pandoc_f_"))
    process = None
    events = queue.Queue()
    try:
        rp = work / "receptor.pdbqt"
        lp = work / "ligand.pdbqt"
        rp.write_bytes(receptor)
        lp.write_bytes(ligand)
        output = work / "docked.pdbqt"
        vina = os.environ.get("PANDOC_F_VINA", "vina")
        command = [vina, "--receptor", str(rp), "--ligand", str(lp),
                   "--center_x", str(center[0]), "--center_y", str(center[1]),
                   "--center_z", str(center[2]), "--size_x", str(size[0]),
                   "--size_y", str(size[1]), "--size_z", str(size[2]),
                   "--exhaustiveness", str(exhaustiveness),
                   "--num_modes", str(poses), "--seed", str(seed),
                   "--cpu", str(max(1, os.cpu_count() or 1)),
                   "--out", str(output)]
        process = subprocess.Popen(command, cwd=work, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   errors="replace", bufsize=1)
        def read_lines():
            try:
                for line in process.stdout:
                    events.put(line.rstrip("\r\n"))
            finally:
                events.put(None)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        yield event("started", pid=process.pid)
        deadline = time.monotonic() + int(os.environ.get("PANDOC_F_TIMEOUT", "3600"))
        finished_reading = False
        while not finished_reading or process.poll() is None:
            if time.monotonic() > deadline:
                raise TimeoutError("Direct runtime exceeded its time limit")
            try:
                line = events.get(timeout=0.5)
                if line is None:
                    finished_reading = True
                else:
                    yield event("log", message=line[:2000])
            except queue.Empty:
                yield event("heartbeat")
        code = process.wait()
        if code != 0 or not output.exists():
            raise RuntimeError("Vina failed with exit code " + str(code))
        if output.stat().st_size > MAX_BYTES:
            raise RuntimeError("Docking result exceeds 25 MB")
        manifest = {"runtime": "Computer F", "seed": seed, "center": center,
                    "size": size, "exhaustiveness": exhaustiveness,
                    "num_modes": poses, "cpu": max(1, os.cpu_count() or 1)}
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(output, "docked.pdbqt")
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        yield event("complete", filename="computer_f_results.zip",
                    zip_base64=base64.b64encode(bundle.getvalue()).decode("ascii"))
    except Exception as exc:
        yield event("error", message=str(exc)[:500])
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        shutil.rmtree(work, ignore_errors=True)
        SLOTS.release()


@app.post("/v1/dock")
async def dock(
    receptor: UploadFile = File(...),
    ligand: UploadFile = File(...),
    center_x: float = Form(...), center_y: float = Form(...), center_z: float = Form(...),
    size_x: float = Form(...), size_y: float = Form(...), size_z: float = Form(...),
    exhaustiveness: int = Form(8), poses: int = Form(9), seed: int = Form(2026),
    x_api_key: str | None = Header(default=None),
):
    import math
    authorize(x_api_key)
    center = [center_x, center_y, center_z]
    size = [size_x, size_y, size_z]
    if (not all(math.isfinite(v) for v in center + size)
            or any(not 0 < v <= 60 for v in size)
            or not 1 <= exhaustiveness <= 64
            or not 1 <= poses <= 20 or not 1 <= seed <= 2147483647):
        raise HTTPException(422, "Invalid docking parameters")
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(409, "Computer F busy — no queue; retry later")
    try:
        r = await receptor.read(MAX_BYTES + 1)
        l = await ligand.read(MAX_BYTES + 1)
        if not r or not l or len(r) > MAX_BYTES or len(l) > MAX_BYTES:
            raise HTTPException(413, "Input must be nonempty and at most 25 MB per file")
        if b"ATOM" not in r and b"HETATM" not in r:
            raise HTTPException(422, "Receptor does not look like PDBQT")
        if b"ATOM" not in l and b"HETATM" not in l:
            raise HTTPException(422, "Ligand does not look like PDBQT")
        return StreamingResponse(
            execute(r, l, center, size, exhaustiveness, poses, seed),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    except BaseException:
        SLOTS.release()
        raise
