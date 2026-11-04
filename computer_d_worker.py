"""Windows Computer D: Vina-only outbound worker. Never run as Administrator."""
from __future__ import annotations
import base64
import io
import json
import math
import os
import re
import subprocess
import queue
import threading
import time
import zipfile
from pathlib import Path
import requests
from cryptography.fernet import Fernet

REPO=os.environ.get("PANDOC_GITHUB_REPOSITORY","peterDataScientia/PanDoc")
TOKEN=os.environ.get("GITHUB_TOKEN","").strip()
KEY=os.environ.get("PANDOC_JOB_KEY","").strip()
VINA=Path(os.environ.get("PANDOC_VINA_EXE",r"C:\Program Files (x86)\vina_1.2\vina.exe"))
WORK=Path(os.environ.get("PANDOC_D_WORK_DIR",str(Path.home()/"PanDoc-Vina-Worker")))
API="https://api.github.com/repos/"+REPO
PREFIX="pandoc-d-job-"

def api(method,suffix,**kw):
    response=requests.request(method,API+suffix,timeout=(15,90),headers={
        "Authorization":"Bearer "+TOKEN,"Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2022-11-28"},**kw)
    if response.status_code>=400:
        raise RuntimeError("GitHub API "+str(response.status_code)+": "+response.text[:200])
    return response.json() if response.content and "json" in response.headers.get("content-type","") else None

def get_file(branch,path):
    return base64.b64decode(api("GET","/contents/"+path,params={"ref":branch})["content"])

def put_file(branch,path,data,message):
    old=None
    try: old=api("GET","/contents/"+path,params={"ref":branch})["sha"]
    except RuntimeError as exc:
        if "404" not in str(exc): raise
    request={"branch":branch,"message":message,"content":base64.b64encode(data).decode()}
    if old: request["sha"]=old
    api("PUT","/contents/"+path,json=request)

def unpack(encrypted,folder):
    raw=Fernet(KEY.encode()).decrypt(encrypted)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.infolist())>1200: raise ValueError("Too many input files")
        if sum(i.file_size for i in z.infolist())>80_000_000: raise ValueError("Oversized input archive")
        for item in z.infolist():
            if item.is_dir(): continue
            dest=(folder/item.filename).resolve()
            if folder.resolve() not in dest.parents or item.file_size>30_000_000:
                raise ValueError("Unsafe input archive path")
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(z.read(item))

def validate(config,folder):
    receptor=(folder/config["receptor"]).resolve()
    if folder.resolve() not in receptor.parents or not receptor.is_file() or receptor.suffix.lower()!=".pdbqt":
        raise ValueError("Invalid receptor")
    if not 1<=len(config["ligands"])<=500: raise ValueError("Invalid ligand count")
    if not 1<=len(config["seeds"])<=5 or any(not 1<=int(s)<=2147483647 for s in config["seeds"]):
        raise ValueError("Invalid seeds")
    if not 1<=int(config["poses"])<=20 or not 1<=int(config["exhaustiveness"])<=64:
        raise ValueError("Invalid docking parameters")
    for field in ("center","size"):
        if len(config[field])!=3 or any(not math.isfinite(float(v)) for v in config[field]):
            raise ValueError("Invalid docking box")
    if any(not 0<float(v)<=60 for v in config["size"]):
        raise ValueError("Invalid box size")
    for ligand in config["ligands"]:
        p=(folder/ligand["path"]).resolve()
        if folder.resolve() not in p.parents or not p.is_file() or p.suffix.lower()!=".pdbqt":
            raise ValueError("Invalid ligand file")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",str(ligand["id"])):
            raise ValueError("Invalid ligand ID")
    return receptor

def score_table(path):
    values=re.findall(r"^REMARK VINA RESULT:\s*(-?\d+(?:\.\d+)?)",path.read_text(errors="replace"),re.M)
    if not values: raise RuntimeError("Vina produced no scored poses")
    return [float(v) for v in values]

def run_vina_live(args, folder, on_update, timeout_seconds=3600, publish_interval=15, local_log=None):
    """Publish actual Vina stdout and heartbeat while its subprocess is alive."""
    output = []
    events = queue.Queue()
    local_stream = open(local_log, "a", encoding="utf-8", buffering=1) if local_log else None
    proc = subprocess.Popen(
        args, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    def reader():
        try:
            for line in proc.stdout:
                events.put(line.rstrip("\r\n"))
        finally:
            events.put(None)
    threading.Thread(target=reader, daemon=True).start()
    start = time.monotonic()
    last_publish = start
    dirty = False
    eof = False
    try:
        while proc.poll() is None or not eof or not events.empty():
            if time.monotonic() - start > timeout_seconds:
                raise RuntimeError("Vina search exceeded 1 hour")
            try:
                line = events.get(timeout=0.6)
                if line is None:
                    eof = True
                elif line:
                    output.append(line)
                    print(line, flush=True)
                    if local_stream:
                        local_stream.write(line + "\n")
                        local_stream.flush()
                    dirty = True
            except queue.Empty:
                pass
            now = time.monotonic()
            if now - last_publish >= publish_interval:
                on_update("\n".join(output[-120:]), now-start, dirty)
                last_publish = now
                dirty = False
        code = proc.wait()
        # Short searches should not trigger a separate GitHub commit simply
        # because Vina exited. Batch progress at the worker level instead.
        if time.monotonic() - start >= publish_interval:
            on_update("\n".join(output[-120:]), time.monotonic()-start, dirty)
        return code, "\n".join(output)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()
        if local_stream:
            local_stream.close()


def run_job(branch,job_id):
    base=f".pandoc_d_jobs/{job_id}/"
    folder=WORK/"jobs"/job_id
    folder.mkdir(parents=True,exist_ok=True)
    cipher=Fernet(KEY.encode())
    logs=[]
    completed=0
    def status(state,**extra):
        put_file(branch,base+"status.json",json.dumps({"state":state,"completed":completed,**extra}).encode(),"Computer D "+state)
    def log():
        put_file(branch,base+"log.bin",cipher.encrypt("\n".join(logs)[-20000:].encode()),"Encrypted Vina log")
    try:
        status("running",current="Loading encrypted prepared structures")
        unpack(get_file(branch,base+"input.bin"),folder)
        config=json.loads((folder/"config.json").read_text())
        receptor=validate(config,folder)
        total=len(config["ligands"])*len(config["seeds"])
        # Use every logical processor visible to Windows; no 4-thread or
        # exhaustiveness-derived cap. Vina manages the search threads.
        cpu=max(1, os.cpu_count() or 1)
        print(f"Computer D: {cpu} logical CPUs visible; exhaustiveness={config['exhaustiveness']}",flush=True)
        # GitHub is a remote control plane, not a live per-second log stream.
        # Limit network uploads while keeping all scientific output in local Vina log.
        records=[]
        last_report=time.monotonic()
        last_cancel_check=0.0
        for ligand in config["ligands"]:
            for seed in config["seeds"]:
                # Check cancellation every 10 seconds rather than making an
                # extra GitHub request before every short ligand search.
                if time.monotonic() - last_cancel_check >= 10.0:
                    last_cancel_check=time.monotonic()
                    try: get_file(branch,base+"cancel.json")
                    except RuntimeError as exc:
                        if "404" not in str(exc):raise
                    else:
                        status("cancelled",total=total)
                        return
                out=f"{ligand['id']}_seed{int(seed)}.pdbqt"
                args=[str(VINA),"--receptor",str(receptor),"--ligand",str((folder/ligand["path"]).resolve()),
                      "--center_x",str(float(config["center"][0])),"--center_y",str(float(config["center"][1])),
                      "--center_z",str(float(config["center"][2])),"--size_x",str(float(config["size"][0])),
                      "--size_y",str(float(config["size"][1])),"--size_z",str(float(config["size"][2])),
                      "--cpu",str(cpu),"--exhaustiveness",str(int(config["exhaustiveness"])),
                      "--num_modes",str(int(config["poses"])),"--seed",str(int(seed)),
                      "--out",str(folder/out)]
                current=f"{ligand['id']} / seed {seed}"
                search_start=time.monotonic()
                logs.append(f"Docking {ligand['name']} | seed {seed} | CPU threads {cpu} | exhaustiveness {config['exhaustiveness']}")
                if completed == 0 or time.monotonic()-last_report >= 12:
                    status("running", current=current, total=total,
                           active_ligand=str(ligand["name"]), active_seed=int(seed))
                    log()
                    last_report=time.monotonic()
                def publish_live(output, elapsed, changed):
                    # Preserve full raw Vina stdout locally in vina_live.log,
                    # but never duplicate its citation banner, paths or progress
                    # ruler in the user-facing encrypted activity log.
                    status("running", current=current, total=total,
                           active_ligand=str(ligand["name"]), active_seed=int(seed),
                           elapsed_seconds=round(elapsed, 1))
                returncode, output = run_vina_live(args, folder, publish_live,
                                                    local_log=folder/"vina_live.log")
                search_elapsed=time.monotonic()-search_start
                if returncode or not (folder/out).is_file():
                    logs.append(f"Failed {current} after {search_elapsed:.1f} s (exit code {returncode})")
                    log()
                    raise RuntimeError("Vina returned failure for "+current)
                scores=score_table(folder/out)
                logs.append(
                    f"Completed {ligand['name']} | seed {seed} | "
                    f"{search_elapsed:.1f} s | best affinity {min(scores):.3f} kcal/mol | "
                    f"{len(scores)} poses"
                )
                records.append({"ligand":str(ligand["name"]),"ligand_id":str(ligand["id"]),
                                "seed":int(seed),"pdbqt":out,"scores":scores})
                completed+=1
                if time.monotonic()-last_report >= 12 and completed < total:
                    status("running",current=current,total=total)
                    log()
                    last_report=time.monotonic()
        log()
        (folder/"raw_results.json").write_text(json.dumps(records,indent=2))
        (folder/"worker.log").write_text("\n".join(logs))
        # Full unmodified stdout remains available separately in vina_live.log.
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as z:
            for path in folder.rglob("*"):
                if path.is_file(): z.write(path,path.relative_to(folder).as_posix())
        encrypted=cipher.encrypt(stream.getvalue())
        if len(encrypted)>35_000_000: raise RuntimeError("Result exceeds 35 MB transport limit")
        put_file(branch,base+"result.bin",encrypted,"Encrypted Computer D docking result")
        status("completed",total=total,current="Vina docking finished")
    except Exception as exc:
        logs.append("ERROR: "+str(exc))
        try: log()
        except Exception: pass
        try: status("failed",error=str(exc)[:500])
        except Exception: pass
        print("Job failed:",job_id,exc,flush=True)

def load_credentials():
    """Store secrets in the current Windows user's Credential Manager."""
    global TOKEN, KEY
    from getpass import getpass
    import sys
    try:
        import keyring
    except ImportError as exc:
        raise SystemExit(
            "Credential Manager support is required: py -3.13 -m pip install keyring"
        ) from exc
    backend = keyring.get_keyring()
    if not backend.__class__.__module__.startswith("keyring.backends.Windows"):
        raise SystemExit(
            "A Windows Credential Manager backend is required; refusing to store "
            "secrets in a plaintext or fallback keyring."
        )
    service = "PanDoc-Computer-D:" + REPO
    if "--reset-credentials" in sys.argv:
        for account in ("GITHUB_TOKEN", "PANDOC_JOB_KEY"):
            try:
                keyring.delete_password(service, account)
            except keyring.errors.PasswordDeleteError:
                pass
        print("Saved Computer D credentials cleared from Windows Credential Manager.")
        raise SystemExit(0)

    token_saved = keyring.get_password(service, "GITHUB_TOKEN")
    key_saved = keyring.get_password(service, "PANDOC_JOB_KEY")
    if not TOKEN:
        TOKEN = (token_saved or getpass("GitHub token (hidden, first run only): ")).strip()
    if not KEY:
        KEY = (key_saved or getpass("PANDOC_JOB_KEY (hidden, first run only): ")).strip()
    if not TOKEN or not KEY:
        raise SystemExit("Both credentials are required.")
    try:
        Fernet(KEY.encode())
    except (ValueError, TypeError) as exc:
        raise SystemExit("PANDOC_JOB_KEY is not a valid Fernet key.") from exc

    # Confirm GitHub access before saving a newly entered token.
    try:
        api("GET", "")
    except (RuntimeError, requests.RequestException) as exc:
        raise SystemExit(
            "GitHub authentication failed; credentials were not saved: " + str(exc)
        ) from exc
    if not token_saved and not os.environ.get("GITHUB_TOKEN"):
        keyring.set_password(service, "GITHUB_TOKEN", TOKEN)
    if not key_saved and not os.environ.get("PANDOC_JOB_KEY"):
        keyring.set_password(service, "PANDOC_JOB_KEY", KEY)
    print("Computer D credentials loaded from Windows Credential Manager.")


def main():
    load_credentials()
    if not VINA.is_file():raise SystemExit("Cannot find Vina executable: "+str(VINA))
    WORK.mkdir(parents=True,exist_ok=True)
    print("Computer D ready. Vina:",VINA,flush=True)
    while True:
        try:
            refs=api("GET","/git/matching-refs/heads/"+PREFIX) or []
            for ref in refs:
                branch=ref["ref"].removeprefix("refs/heads/")
                if not re.fullmatch(r"pandoc-d-job-[0-9a-f]{32}",branch):continue
                job_id=branch.removeprefix(PREFIX)
                try:
                    info=json.loads(get_file(branch,f".pandoc_d_jobs/{job_id}/status.json"))
                    if info.get("state") in ("running","completed","failed","cancelled"):continue
                except RuntimeError as exc:
                    if "404" not in str(exc):raise
                run_job(branch,job_id)
        except (requests.RequestException,RuntimeError) as exc:
            print("Queue error:",exc,flush=True)
        time.sleep(4)

if __name__=="__main__":main()
