import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem
from streamlit.testing.v1 import AppTest

from pandoc import core
from pandoc.worker import run


def test_small_real_docking_workflow(tmp_path):
    peptide=Chem.AddHs(Chem.MolFromSequence('AG'))
    assert AllChem.EmbedMolecule(peptide,randomSeed=2026)==0
    AllChem.MMFFOptimizeMolecule(peptide)
    pdb=Chem.MolToPDBBlock(Chem.RemoveHs(peptide))
    receptor=core.prepare_receptor(pdb,tmp_path/'prepared')
    ligand=core.molecule(smiles='CCO')
    core.write_ligand(ligand,tmp_path/'ethanol.pdbqt')
    job=tmp_path/'jobs'/'test'
    job.mkdir(parents=True)
    config=dict(receptor=str(receptor),center=[0,0,0],size=[12,12,12],exhaustiveness=1,poses=2,seeds=[2026],cpu=1,
                reference=str(tmp_path/'ethanol.sdf'),ligands=[dict(id='ethanol',name='Ethanol',path=str(tmp_path/'ethanol.pdbqt'))])
    (job/'config.json').write_text(json.dumps(config))
    run(job)
    state=json.loads((job/'status.json').read_text())
    assert state['state']=='completed',state
    assert state['completed']==state['total']==1
    rows=json.loads((job/'results.json').read_text())
    assert len(rows)>=1
    assert all(r['reference_rmsd_A']>=0 for r in rows)
    assert all(m is not None for m in Chem.SDMolSupplier(str(job/'ethanol_seed2026.sdf')))
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py').run()
    for key,value in dict(root=str(tmp_path),selected_pdb=pdb,prepared_pdb=pdb,receptor_path=str(receptor),
                          preparation_id='test',reference_pdb=Chem.MolToPDBBlock(Chem.RemoveHs(ligand)),
                          reference_path=str(tmp_path/'ethanol.sdf'),reference_pdbqt=str(tmp_path/'ethanol.pdbqt'),
                          reference_id='reference',center=[0.,0.,0.],size=[12.,12.,12.]).items():
        at.session_state[key]=value
    for stage in ['2 · Prepare structures','3 · Validate docking','4 · Run experiment','5 · Explore results']:
        at.sidebar.radio[0].set_value(stage).run()
        assert not list(at.exception)
        assert not list(at.error),list(at.error)


def test_guided_screens_render(tmp_path):
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py').run()
    assert not list(at.exception)
    for stage in ['2 · Prepare structures','3 · Validate docking','4 · Run experiment','5 · Explore results']:
        at.sidebar.radio[0].set_value(stage).run()
        assert not list(at.exception)
        assert not list(at.error)
