import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pandoc import core


def record(serial, name, res='ALA', alt='', x=0.):
    return f'ATOM  {serial:5d} {name:^4s}{alt or " "}{res:>3s} A   1    {x:8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{20.:6.2f}          {name[0]:>2s}'


class StructureTests(unittest.TestCase):
    def test_altloc_selection_does_not_mix_conformers(self):
        pdb='\n'.join([record(1,'N'),record(2,'CA',alt='A',x=1),record(3,'CA',alt='B',x=2)])
        selected=core.select(pdb, ['A:1:ALA'], per_residue={'A:1:ALA':'B'})
        aa=core.atoms(selected)
        self.assertEqual(len(aa),2)
        self.assertEqual(aa[1]['xyz'][0],2.)
        self.assertEqual(aa[1]['alt'],'')

    def test_inspection_detects_incomplete_sidechain(self):
        row=core.inspect('\n'.join(record(i+1,n) for i,n in enumerate(['N','CA','C','O'])))[0]
        self.assertEqual(row['missing_estimate'],1)

    def test_first_model_only(self):
        self.assertEqual(len(core.atoms(record(1,'N')+'\nENDMDL\n'+record(2,'CA'))),1)

    def test_box_uses_heavy_atoms(self):
        pdb=record(1,'C',x=0)+'\n'+record(2,'O',x=4)+'\n'+record(3,'H',x=100)
        center,size=core.box(pdb,5)
        self.assertEqual(center,[2.,0.,0.])
        self.assertEqual(size,[14.,10.,10.])

    def test_protocol_changes_invalidate_hash(self):
        self.assertNotEqual(core.digest('receptor',[20,20,20]),core.digest('receptor',[21,20,20]))

    def test_invalid_box_and_seed_rejected(self):
        base=dict(center=[0,0,0],size=[20,20,20],exhaustiveness=8,poses=9,seeds=[2026])
        core.validate_config(base)
        for change in ({'center':[float('nan'),0,0]},{'size':[0,20,20]},{'seeds':[-1]}):
            with self.assertRaises(ValueError):
                core.validate_config({**base,**change})


class ChemistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from rdkit import Chem
        except ImportError:
            raise unittest.SkipTest('RDKit unavailable')

    def test_rmsd_does_not_align_away_translation(self):
        from rdkit import Chem
        from rdkit.Chem import AllChem
        reference=core.molecule(smiles='CCO')
        pose=Chem.Mol(reference)
        conf=pose.GetConformer()
        for i in range(pose.GetNumAtoms()):
            point=conf.GetAtomPosition(i)
            point.x+=5
            conf.SetAtomPosition(i,point)
        self.assertAlmostEqual(core.reference_rmsd(reference,pose),5.,places=5)

    def test_symmetry_equivalent_atom_mapping(self):
        from rdkit import Chem
        ref=core.molecule(smiles='CC(=O)[O-]')
        reordered=Chem.RenumberAtoms(ref,list(reversed(range(ref.GetNumAtoms()))))
        self.assertAlmostEqual(core.reference_rmsd(ref,reordered),0.,places=5)

    def test_reference_coordinates_survive_bond_assignment(self):
        from rdkit import Chem
        ref=core.molecule(smiles='CCO')
        heavy=Chem.RemoveHs(ref)
        pdb=Chem.MolToPDBBlock(heavy)
        rebuilt=Chem.RemoveHs(core.reference_from_pdb(pdb,'CCO'))
        self.assertLess(core.reference_rmsd(heavy,rebuilt),0.002)

    def test_meeko_ligand_roundtrip(self):
        try:
            import meeko
        except ImportError:
            self.skipTest('Meeko unavailable')
        from meeko import PDBQTMolecule,RDKitMolCreate
        with TemporaryDirectory() as d:
            ref=core.molecule(smiles='CCO')
            path=Path(d)/'ligand.pdbqt'
            core.write_ligand(ref,path)
            mol=RDKitMolCreate.from_pdbqt_mol(PDBQTMolecule.from_file(str(path),skip_typing=True))[0]
            self.assertLess(core.reference_rmsd(ref,mol),0.002)

    def test_mismatch_explains_difference(self):
        from rdkit import Chem
        ref=Chem.MolToPDBBlock(Chem.RemoveHs(core.molecule(smiles='CCO')))
        report=core.ligand_comparison(ref,'CC')
        self.assertFalse(report['valid'])
        self.assertEqual(report['pdb_heavy_atoms'],3)
        self.assertEqual(report['smiles_heavy_atoms'],2)
        self.assertIn('fewer',report['message'])
        self.assertTrue(core.ligand_comparison(ref,'CCO')['valid'])
        self.assertFalse(core.ligand_comparison(ref,'CCO.[Na+]')['valid'])
        self.assertIn('disconnected',core.ligand_comparison(ref,'CCO.[Na+]')['message'])

    def test_ccd_atom_names_only_used_when_matching(self):
        pdb=record(1,'C1')+'\n'+record(2,'O1')
        report=core.ccd_atom_name_check(pdb,{'heavy_atom_names':['C1','O1','N1']})
        self.assertTrue(report['reliable'])
        self.assertEqual(report['missing'],['N1'])
        self.assertFalse(core.ccd_atom_name_check(pdb,{'heavy_atom_names':['XX','YY']})['reliable'])

    def test_ccd_lookup_handles_missing_component(self):
        from unittest.mock import patch,Mock
        with patch('requests.get',return_value=Mock(status_code=404)):
            with self.assertRaisesRegex(ValueError,'No CCD definition'):
                core.fetch_ccd('LIG')

    def test_ccd_lookup_reads_stereochemical_descriptor(self):
        from unittest.mock import patch,Mock
        response=Mock(status_code=200)
        response.json.return_value={'chem_comp':{'name':'ethanol','formula':'C2 H6 O','pdbx_formal_charge':0},'rcsb_chem_comp_descriptor':{'SMILES_stereo':'CCO'}}
        response.text='data_TEST\nloop_\n_chem_comp_atom.atom_id\n_chem_comp_atom.type_symbol\nC1 C\nC2 C\nO1 O\nH1 H\n'
        with patch('requests.get',return_value=response):
            result=core.fetch_ccd('EOH')
        self.assertEqual(result['smiles'],'CCO')
        self.assertEqual(result['heavy_atom_names'],['C1','C2','O1'])


if __name__=='__main__':
    unittest.main()


def test_repair_terminal_oxygen_preserves_original_coordinates():
    import pytest
    pytest.importorskip('pdbfixer')
    from pandoc import core
    import numpy as np
    pdb = '''ATOM      1  N   CYS A 687       1.983  59.716  18.382  1.00117.90           N
ATOM      2  CA  CYS A 687       3.000  60.775  18.317  1.00141.23           C
ATOM      3  C   CYS A 687       2.385  62.146  18.573  1.00167.65           C
ATOM      4  O   CYS A 687       1.589  62.301  19.498  1.00126.77           O
ATOM      5  CB  CYS A 687       3.809  60.752  17.021  1.00141.38           C
ATOM      6  SG  CYS A 687       2.837  60.572  15.502  1.00145.20           S
END
'''
    original = {a['name']: a for a in core.atoms(pdb)}
    repaired = {a['name']: a for a in core.atoms(core.repair_heavy_atoms(pdb))}
    for name, atom in original.items():
        assert np.allclose(atom['xyz'], repaired[name]['xyz'], atol=0.001)
    assert np.linalg.norm(np.array(repaired['OXT']['xyz']) - repaired['O']['xyz']) > 2
    assert abs(np.linalg.norm(np.array(repaired['OXT']['xyz']) - repaired['C']['xyz']) - 1.25) < 0.01
