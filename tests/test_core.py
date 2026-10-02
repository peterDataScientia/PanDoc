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


if __name__=='__main__':
    unittest.main()
