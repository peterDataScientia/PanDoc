"""Live diagnostic redocking of deposited 5E9C glycan; not a validated protocol.

Uses modeled protein repairs and predicted pH 5.5 states; all such states
require human review before publication claims. Run only when explicitly
requested; does not run in the ordinary unit-test suite.
"""
import json
import tempfile
from pathlib import Path

import requests

from pandoc import core, glycans, phprep, worker


def run():
    response=requests.get('https://files.rcsb.org/download/5E9C.pdb', timeout=50)
    response.raise_for_status()
    pdb=response.text
    options=core.glycan_chain_candidates(pdb)
    names=[name for name in options if 'chain C' in name]
    if len(names)!=1 or len(options[names[0]])!=4:
        raise ValueError(f'Unexpected glycan identification: {options}')
    glycan_pdb=core.select(pdb,options[names[0]])
    reference,audit=glycans.reconstruct_glycan(glycan_pdb,pdb)
    components=core.inspect(pdb)
    receptor_ids=[r['residue'] for r in components if r['kind']=='Protein']
    receptor_pdb=core.select(pdb,receptor_ids)
    center,size=core.box(glycan_pdb)
    # This diagnostic does not silently ignore missing protein atoms.
    repaired=core.repair_heavy_atoms(receptor_pdb)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        predicted,_=phprep.predict_protein_states(repaired,root/'propka',5.5)
        ambiguous=[r for r in predicted if r['review_required']]
        print('pH 5.5 protonation-review flags:',json.dumps(ambiguous))
        # Only unambiguous PROPKA assignments are automatically applied.
        assignments=phprep.template_assignments(
            [r for r in predicted if not r['review_required']])
        rec_path=core.prepare_receptor(
            repaired,root/'receptor',template_assignments=assignments)
        ligand_path=root/'reference.pdbqt'
        core.write_ligand(reference,ligand_path)
        results=worker._run_search(
            root,
            dict(receptor=str(rec_path),reference=str(ligand_path.with_suffix('.sdf')),
                 center=center,size=size,exhaustiveness=8,poses=5),
            dict(name='5E9C_DP4',id='dp4',path=str(ligand_path)),
            seed=2026,cpu_threads=2)
        report=dict(pdb='5E9C',ph=5.5,center=center,size=size,
                    ligand_audit=audit,protonation_review_flags=ambiguous,
                    docking_results=results)
        print('5E9C_RED0CK_REPORT='+json.dumps(report,default=str))
        return report


if __name__=='__main__':
    run()
