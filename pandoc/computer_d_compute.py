"""Computer D Vina-only backend; encrypted GitHub branch transport."""
from __future__ import annotations
import base64
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet
from rdkit import Chem
from meeko import PDBQTMolecule, RDKitMolCreate
from .github_compute import _payload, _request, _repo_url, validate_key, DEFAULT_REPOSITORY
from .compute import ComputeBackendError

class ComputerDError(ComputeBackendError):
    pass

class ComputerDCompute:
    name = "computer-d-vina"

    def __init__(self, token, job_key, repository=DEFAULT_REPOSITORY):
        if not (token or "").strip():
            raise ComputerDError("GITHUB_TOKEN is missing")
        self.token = token.strip()
        self.key = validate_key(job_key)
        self.repository = repository

    def _content(self, job, filename):
        url = _repo_url(self.repository, "/contents/.pandoc_d_jobs/" + job["job_id"] + "/" + filename)
        obj = _request(self.token, "GET", url, params={"ref":job["branch"]}).json()
        return base64.b64decode(obj["content"])

    def submit(self, root, config):
        from .core import validate_config
        validate_config(config)
        if not 1 <= len(config.get("ligands",[])) <= 500:
            raise ComputerDError("Computer D accepts 1-500 prepared ligands per run.")
        job_id = uuid.uuid4().hex
        branch = "pandoc-d-job-" + job_id
        encrypted = _payload(config, self.key)
        if len(encrypted) > 35_000_000:
            raise ComputerDError("Computer D job exceeds 35 MB.")
        repo = _request(self.token,"GET",_repo_url(self.repository,"")).json()
        head = _request(self.token,"GET",_repo_url(self.repository,"/git/ref/heads/"+quote(repo["default_branch"],safe=""))).json()
        _request(self.token,"POST",_repo_url(self.repository,"/git/refs"),
                 json={"ref":"refs/heads/"+branch,"sha":head["object"]["sha"]})
        try:
            _request(self.token,"PUT",_repo_url(self.repository,"/contents/.pandoc_d_jobs/"+job_id+"/input.bin"),
                     json={"branch":branch,"message":"Encrypted Computer D job "+job_id[:8],
                           "content":base64.b64encode(encrypted).decode()})
        except Exception:
            _request(self.token,"DELETE",_repo_url(self.repository,"/git/refs/heads/"+quote(branch,safe="")))
            raise
        return {"backend":self.name,"job_id":job_id,"branch":branch,"repository":self.repository,"submitted_at":time.time()}

    def status(self, job):
        try:
            return json.loads(self._content(job,"status.json"))
        except Exception as exc:
            if "404" in str(exc):
                return {"state":"queued","completed":0,"message":"Waiting for Computer D"}
            raise ComputerDError(str(exc)) from exc

    def progress(self, job):
        s=self.status(job)
        return {"state":s.get("state","queued"),"steps":[{"name":s.get("current","Computer D Vina"),"status":s.get("state","queued")}]}

    def logs(self, job, tail=80):
        try:
            value=Fernet(self.key.encode()).decrypt(self._content(job,"log.bin")).decode("utf-8",errors="replace")
            return "\n".join(value.splitlines()[-max(1,int(tail)):])
        except Exception:
            return "Waiting for Computer D docking output..."

    def cancel(self, job):
        _request(self.token,"PUT",_repo_url(self.repository,"/contents/.pandoc_d_jobs/"+job["job_id"]+"/cancel.json"),
                 json={"branch":job["branch"],"message":"Request Computer D cancellation","content":base64.b64encode(b'{"cancel":true}').decode()})
        return True

    def materialize(self, job, target):
        if self.status(job).get("state")!="completed":
            raise ComputerDError("Computer D job has not completed.")
        encrypted=self._content(job,"result.bin")
        raw=Fernet(self.key.encode()).decrypt(encrypted)
        target=Path(target)
        target.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist())>200:
                raise ComputerDError("Too many result files")
            for member in archive.infolist():
                if member.is_dir(): continue
                dest=(target/member.filename).resolve()
                if target.resolve() not in dest.parents or member.file_size>60_000_000:
                    raise ComputerDError("Unsafe result archive entry")
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(archive.read(member))
        entries=json.loads((target/"raw_results.json").read_text())
        rows=[]
        ref_path=target/"inputs/reference.sdf"
        reference=None
        if ref_path.is_file():
            reference=next(iter(Chem.SDMolSupplier(str(ref_path),removeHs=False)))
            if reference is None: raise ComputerDError("Unreadable reference SDF.")
        for record in entries:
            pose_file=(target/record["pdbqt"]).resolve()
            if target.resolve() not in pose_file.parents or not pose_file.is_file():
                raise ComputerDError("Invalid docked PDBQT reference")
            mols=RDKitMolCreate.from_pdbqt_mol(PDBQTMolecule.from_file(str(pose_file),skip_typing=True))
            if len(mols)!=1 or mols[0] is None:
                raise ComputerDError("Meeko could not reconstruct "+record["pdbqt"])
            mol=mols[0]
            conformers=list(mol.GetConformers())
            scores=record["scores"]
            if len(conformers)!=len(scores):
                raise ComputerDError("Vina pose count differs from affinity count")
            sdf_name=pose_file.with_suffix(".sdf").name
            writer=Chem.SDWriter(str(target/sdf_name))
            try:
                for rank,conf in enumerate(conformers,1):
                    pose=Chem.Mol(mol)
                    pose.RemoveAllConformers()
                    pose.AddConformer(Chem.Conformer(conf),assignId=True)
                    score=float(scores[rank-1])
                    pose.SetProp("vina_score_kcal_mol",str(score))
                    row={"ligand":record["ligand"],"ligand_id":record["ligand_id"],"seed":int(record["seed"]),
                         "rank":rank,"score_kcal_mol":score,"sdf":sdf_name}
                    if reference is not None:
                        from .core import reference_rmsd
                        row["reference_rmsd_A"]=reference_rmsd(reference,pose)
                        pose.SetProp("reference_rmsd_A",str(row["reference_rmsd_A"]))
                    writer.write(pose)
                    rows.append(row)
            finally:
                writer.close()
        (target/"results.json").write_text(json.dumps(rows,indent=2))
        (target/"status.json").write_text(json.dumps({"state":"completed","completed":len(entries),"total":len(entries)}))
        return target

    def cleanup(self, job):
        _request(self.token,"DELETE",_repo_url(self.repository,"/git/refs/heads/"+quote(job["branch"],safe="")))
