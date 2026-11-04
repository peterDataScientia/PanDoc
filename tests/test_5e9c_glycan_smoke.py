"""5E9C glycan smoke/integration checks; run with real RCSB CCD connectivity."""
import requests
from pandoc import core, glycans

def main():
    response=requests.get('https://files.rcsb.org/download/5E9C.pdb',timeout=45)
    response.raise_for_status()
    pdb=response.text
    assert 'LINK' in pdb, '5E9C PDB does not contain expected LINK records'
    candidates=core.glycan_chain_candidates(pdb)
    keys=[key for key in candidates if 'chain C' in key]
    assert len(keys)==1, f'Expected chain C candidate; observed {candidates}'
    selected=candidates[keys[0]]
    assert len(selected)==4, f'Expected four glycan residues, observed {selected}'
    ref=core.select(pdb,selected)
    molecule,audit=glycans.reconstruct_glycan(ref,pdb)
    assert len(audit['residues'])==4
    assert audit['links']>=3
    assert molecule.GetNumConformers()==1
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as path:
        target=Path(path)/'reference.pdbqt'
        core.write_ligand(molecule,target)
        assert target.stat().st_size>0
        assert target.with_suffix('.sdf').stat().st_size>0
    print('5E9C glycan preparation succeeded',audit)
    print('NOTE: This tests preparation, not redocking RMSD or Vina suitability.')

def test_live_5e9c_glycan_preparation():
    """Run against deposited 5E9C and official CCDs in GitHub Actions."""
    main()

def test_live_5e9c_redocking_diagnostic():
    """Attempt whole-glycan pH-aware Vina recovery; report rather than assume RMSD."""
    from scripts.validate_5e9c_redocking import run
    result=run()
    assert len(result['docking_results']) >= 1
    assert all('reference_rmsd_A' in row for row in result['docking_results'])

if __name__=='__main__':
    main()
