"""Experimental Menin 4X5Y PanDoc docking; retains all scores, structures and failures."""
import os, json, csv, hashlib, traceback
from pathlib import Path
import requests
from rdkit import Chem
from pandoc import core, phprep, worker

ROOT=Path("menin_4x5y_results")
ROOT.mkdir(exist_ok=True)
SEEDS=[2026,2027,2028]
LIGANDS=[
 ("Afatinib","CHEMBL1173655","CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1",5.972459),
 ("Abacavir","CHEMBL1380","Nc1nc(NC2CC2)c2ncn([C@H]3C=C[C@@H](CO)C3)c2n1",5.970041),
 ("Abiraterone","CHEMBL254328","C[C@]12CC[C@H](O)CC1=CC[C@@H]1[C@@H]2CC[C@]2(C)C(c3cccnc3)=CC[C@@H]12",5.593768)
]
manifest=dict(pdb="4X5Y",protein_chain="A",reference_ligand="3XW",pH=7.4,seeds=SEEDS,exhaustiveness=8,poses_per_seed=5,status="started",note="Diagnostics only; Vina scores are not experimental activity.")
def save(name,obj): (ROOT/name).write_text(json.dumps(obj,indent=2,default=str))
def fetch(url):
 r=requests.get(url,timeout=90);r.raise_for_status()
 if len(r.content)<300: raise ValueError("Unexpected empty structure: "+url)
 return r.text
def run():
 pdb=fetch("https://files.rcsb.org/download/4X5Y.pdb")
 (ROOT/"4X5Y.pdb").write_text(pdb)
 manifest["pdb_sha256"]=hashlib.sha256(pdb.encode()).hexdigest()
 ref_lines=[l for l in pdb.splitlines() if l.startswith("HETATM") and l[17:20].strip()=="3XW"]
 if len(ref_lines)<15: raise ValueError("3XW ligand is incomplete or missing")
 ref_pdb="\n".join(ref_lines)+"\nEND\n"
 (ROOT/"3XW_crystal.pdb").write_text(ref_pdb)
 protein="\n".join(l for l in pdb.splitlines() if l.startswith("ATOM") and l[21:22]=="A" and l[16:17] in (" ","A"))+"\nEND\n"
 if protein.count("\nATOM")<1000: raise ValueError("Chain A missing")
 (ROOT/"chain_A_raw.pdb").write_text(protein)
 sdf=fetch("https://files.rcsb.org/ligands/download/3XW_ideal.sdf")
 (ROOT/"3XW_ideal.sdf").write_text(sdf)
 ccd=Chem.MolFromMolBlock(sdf,sanitize=True,removeHs=True)
 if ccd is None: raise ValueError("3XW RCSB CCD chemistry failed")
 ref=core.reference_from_pdb(ref_pdb,Chem.MolToSmiles(ccd,isomericSmiles=True))
 if ref.GetNumHeavyAtoms()!=ccd.GetNumHeavyAtoms(): raise ValueError("Reference heavy atom mismatch")
 core.write_ligand(ref,ROOT/"reference.pdbqt")
 center,size=core.box(ref_pdb,padding=8.0)
 manifest.update(center_A=center,size_A=size,reference_smiles=Chem.MolToSmiles(ccd,isomericSmiles=True))
 save("manifest.json",manifest)
 repaired=core.repair_heavy_atoms(protein)
 (ROOT/"chain_A_repaired.pdb").write_text(repaired)
 states,_=phprep.predict_protein_states(repaired,ROOT/"propka",7.4)
 review=[v for v in states if v.get("review_required")]
 manifest["unreviewed_protonation_states"]=review
 assignments=phprep.template_assignments([v for v in states if not v.get("review_required")])
 manifest["auto_applied_template_assignments"]=assignments
 save("manifest.json",manifest)
 receptor=core.prepare_receptor(repaired,ROOT/"receptor",template_assignments=assignments)
 (ROOT/"prepared_receptor.pdbqt").write_bytes(Path(receptor).read_bytes())
 base=dict(receptor=str(ROOT/"prepared_receptor.pdbqt"),center=center,size=size,exhaustiveness=8,poses=5)
 save("vina_settings.json",dict(**base,seeds=SEEDS))
 results=[]
 for seed in SEEDS:
  print("REFERENCE MI-503 SEED",seed,flush=True)
  results+=worker._run_search(ROOT,dict(**base,reference=str(ROOT/"reference.sdf")),dict(name="MI-503 (3XW)",id="reference",path=str(ROOT/"reference.pdbqt")),seed,cpu_threads=2)
  save("all_scores_and_rmsd.json",results)
 ref_top=[r for r in results if r["ligand_id"]=="reference" and r["rank"]==1]
 manifest["reference_top_pose_rmsd_A"]={str(r["seed"]):r["reference_rmsd_A"] for r in ref_top}
 manifest["reference_best_pose_rmsd_A"]=min(r["reference_rmsd_A"] for r in results)
 manifest["reference_validation_status"]="passed_one_or_more_top_pose_le_2A" if any(r["reference_rmsd_A"]<=2 for r in ref_top) else "not_validated_top_pose_above_2A"
 save("manifest.json",manifest)
 for name,cid,smiles,pred in LIGANDS:
  mol=core.molecule(smiles=smiles)
  ligand_path=ROOT/(name.lower()+".pdbqt")
  core.write_ligand(mol,ligand_path)
  manifest.setdefault("candidates",[]).append(dict(name=name,chembl_id=cid,smiles=smiles,model_predicted_pIC50=pred))
  save("manifest.json",manifest)
  for seed in SEEDS:
   print("CANDIDATE",name,"SEED",seed,flush=True)
   results+=worker._run_search(ROOT,base,dict(name=name,id=name.lower(),path=str(ligand_path)),seed,cpu_threads=2)
   save("all_scores_and_rmsd.json",results)
 rows=[]
 for name,cid,smiles,pred in LIGANDS:
  hits=[r for r in results if r["ligand"]==name]
  if hits:
   best=min(hits,key=lambda x:x["score_kcal_mol"])
   rows.append(dict(drug=name,chembl_id=cid,predicted_pIC50=pred,best_vina_kcal_mol=best["score_kcal_mol"],seed=best["seed"],rank=best["rank"],reference_validation=manifest["reference_validation_status"]))
 with (ROOT/"candidate_summary.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 manifest["status"]="completed";save("manifest.json",manifest)
 print("MENIN_RESULTS="+json.dumps(dict(reference=manifest["reference_top_pose_rmsd_A"],candidates=rows)),flush=True)
try:
 run()
except Exception as e:
 manifest["status"]="failed";manifest["error"]=repr(e);manifest["traceback"]=traceback.format_exc()
 save("manifest.json",manifest)
 raise
