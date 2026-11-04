"""Windows Computer D: Vina-only outbound worker. Never run as Administrator."""
from __future__ import annotations
import base64
import io
import json
import math
import os
import re
import subprocess
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
        if len(z.infolist())>100: raise ValueError("Too many input files")
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
    if not 1<=len(config["ligands"])<=25: raise ValueError("Invalid ligand count")
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
        cpu=max(1,min(4,int(config.get("cpu",4)),int(config["exhaustiveness"])))
        records=[]
        for ligand in config["ligands"]:
            for seed in config["seeds"]:
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
                status("running",current=current,total=total)
                logs.append("Starting "+current+" CPU="+str(cpu))
                log()
                try:
                    result=subprocess.run(args,cwd=folder,capture_output=True,text=True,timeout=3600,
                                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Vina search exceeded 1 hour") from exc
                logs.append((result.stdout+"\n"+result.stderr)[-15000:])
                log()
                if result.returncode or not (folder/out).is_file():
                    raise RuntimeError("Vina returned failure for "+current)
                scores=score_table(folder/out)
                records.append({"ligand":str(ligand["name"]),"ligand_id":str(ligand["id"]),
                                "seed":int(seed),"pdbqt":out,"scores":scores})
                completed+=1
                status("running",current=current,total=total)
        (folder/"raw_results.json").write_text(json.dumps(records,indent=2))
        (folder/"worker.log").write_text("\n".join(logs))
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

def main():
    global TOKEN,KEY
    from getpass import getpass
    if not TOKEN:TOKEN=getpass("GitHub token (hidden): ").strip()
    if not KEY:KEY=getpass("PANDOC_JOB_KEY (hidden): ").strip()
    if not TOKEN or not KEY:raise SystemExit("Token and encryption key required")
    Fernet(KEY.encode())
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
        time.sleep(15)

if __name__=="__main__":main()
