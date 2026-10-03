from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

STANDARD = set('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP ASH GLH CYX LYN'.split())
WATERS = {'HOH', 'WAT', 'DOD'}
EXPECTED = dict(ALA=5, ARG=11, ASN=8, ASP=8, CYS=6, GLN=9, GLU=9, GLY=4, HIS=10, ILE=8, LEU=8, LYS=9, MET=8, PHE=11, PRO=7, SER=6, THR=7, TRP=14, TYR=12, VAL=7)


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def atoms(pdb):
    """Parse first-model PDB while preserving insertion codes and alternate labels."""
    result = []
    for line in pdb.splitlines():
        if line.startswith('ENDMDL'):
            break
        if not line.startswith(('ATOM  ', 'HETATM')):
            continue
        try:
            name = line[12:16].strip()
            element = line[76:78].strip() if len(line) >= 78 else ''
            if not element:
                element = re.sub('[0-9]', '', name)[0]
            result.append(dict(line=line, name=name, alt=line[16:17].strip(), res=line[17:20].strip(), chain=line[21:22].strip(), number=line[22:26].strip(), icode=line[26:27].strip(), xyz=[float(line[30:38]), float(line[38:46]), float(line[46:54])], element=element.upper(), record=line[:6].strip()))
        except (ValueError, IndexError) as exc:
            raise ValueError('Malformed atom record: ' + line[:30]) from exc
    if not result:
        raise ValueError('No atoms found in the first model.')
    return result


def key(a):
    return f"{a['chain'] or '_'}:{a['number']}{a['icode']}:{a['res']}"


def inspect(pdb):
    groups = {}
    for a in atoms(pdb):
        groups.setdefault(key(a), []).append(a)
    rows = []
    for ident, aa in groups.items():
        r = aa[0]['res']
        heavy = {a['name'] for a in aa if a['element'] not in ('H', 'D')}
        expected = EXPECTED.get(r)
        rows.append(dict(residue=ident, chain=aa[0]['chain'], name=r,
                         kind='Protein' if r in STANDARD else 'Water' if r in WATERS else 'Other component',
                         heavy_atoms=len(heavy), missing_estimate=max(0, expected-len(heavy)) if expected else 0,
                         alternatives=','.join(sorted({a['alt'] for a in aa if a['alt']}))))
    return rows


def select(pdb, residues, alternate='A', per_residue=None):
    per_residue = per_residue or {}
    selected = []
    for a in atoms(pdb):
        if key(a) not in residues:
            continue
        wanted = per_residue.get(key(a), alternate)
        if a['alt'] and a['alt'] != wanted:
            continue
        line = a['line']
        selected.append(line[:16] + ' ' + line[17:])
    if not selected:
        raise ValueError('Selection contains no atoms.')
    # Do not carry stale CONECT records after deleting components.
    return '\n'.join(selected) + '\nEND\n'


def normalize_structure(data, suffix):
    if suffix.lower() in ('.cif', '.mmcif'):
        import gemmi
        structure = gemmi.make_structure_from_block(gemmi.cif.read_string(data).sole_block())
        return structure.make_pdb_string()
    atoms(data)
    return data


def molecule(sdf=None, smiles=None, conformer=True):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    if sdf:
        mol = Chem.MolFromMolBlock(sdf, removeHs=False)
    else:
        mol = Chem.MolFromSmiles(smiles or '')
    if mol is None:
        raise ValueError('RDKit could not read the ligand chemistry.')
    if len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError('Use one connected molecule per ligand; separate salts first.')
    mol = Chem.AddHs(mol, addCoords=True)
    if conformer and (not mol.GetNumConformers() or not mol.GetConformer().Is3D()):
        params = AllChem.ETKDGv3()
        params.randomSeed = 2026
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise ValueError('3D conformer generation failed.')
        if AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    Chem.SanitizeMol(mol)
    return mol


def reference_from_pdb(pdb, smiles):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    raw = Chem.MolFromPDBBlock(pdb, removeHs=True, sanitize=False)
    template = Chem.MolFromSmiles(smiles.strip())
    if raw is None or template is None:
        raise ValueError('Could not parse reference ligand or its SMILES.')
    raw = Chem.RemoveHs(raw, sanitize=False)
    template = Chem.RemoveHs(template)
    if raw.GetNumAtoms() != template.GetNumAtoms():
        from collections import Counter
        residues = ', '.join(sorted({key(a) for a in atoms(pdb)}))
        def composition(mol):
            counts = Counter(a.GetSymbol() for a in mol.GetAtoms() if a.GetAtomicNum() > 1)
            return ' '.join(f'{element}:{count}' for element, count in sorted(counts.items()))
        raise ValueError(
            f'Reference ligand {residues} contains {raw.GetNumHeavyAtoms()} heavy atoms '
            f'({composition(raw)}), but the SMILES contains {template.GetNumHeavyAtoms()} '
            f'({composition(template)}). Select the correct ligand and use its exact SMILES. '
            'Check missing ligand atoms and disconnected salt fragments; adding hydrogens will not fix this mismatch.'
        )
    mol = AllChem.AssignBondOrdersFromTemplate(template, raw)
    Chem.SanitizeMol(mol)
    if not mol.GetNumConformers():
        raise ValueError('Reference has no coordinates.')
    return Chem.AddHs(mol, addCoords=True)


def ligand_comparison(pdb, smiles):
    """Explain composition mismatches without guessing missing coordinates."""
    from collections import Counter
    from rdkit import Chem
    aa = atoms(pdb)
    pdb_counts = Counter(a['element'] for a in aa if a['element'] not in ('H', 'D'))
    report = dict(residues=sorted({key(a) for a in aa}), pdb_heavy_atoms=sum(pdb_counts.values()), pdb_elements=dict(pdb_counts), valid=False)
    if not smiles.strip():
        report['message'] = 'Find ligand chemistry below, or enter the exact ligand SMILES.'
        return report
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None:
        report['message'] = 'The SMILES could not be read. Check the text or retrieve the PDB ligand chemistry.'
        return report
    smiles_counts = Counter(a.GetSymbol().upper() for a in mol.GetAtoms() if a.GetAtomicNum() > 1)
    report.update(smiles_heavy_atoms=sum(smiles_counts.values()), smiles_elements=dict(smiles_counts), fragments=len(Chem.GetMolFrags(mol)))
    report['valid'] = pdb_counts == smiles_counts and report['fragments'] == 1
    if report['fragments'] != 1:
        report['message'] = 'The SMILES contains disconnected components. Use the selected ligand alone; remove separate salt or solvent fragments.'
    elif pdb_counts != smiles_counts:
        difference = sum(smiles_counts.values()) - sum(pdb_counts.values())
        if difference > 0:
            report['message'] = f'The SMILES has {difference} more heavy atoms than the selected PDB ligand. Check whether the crystal ligand is incomplete or the SMILES describes another molecule.'
        elif difference < 0:
            report['message'] = f'The SMILES has {-difference} fewer heavy atoms than the selected PDB ligand. Check the ligand selection and the SMILES chemical identity.'
        else:
            report['message'] = 'The total atom counts match, but the chemical elements differ. This SMILES does not match the selected ligand composition.'
    else:
        report['message'] = 'Heavy-atom counts and elements match. Review the chemical identity and state; connectivity will be checked during preparation.'
    return report


def fetch_ccd(component):
    """Retrieve CCD chemistry by component ID; never replace crystal coordinates."""
    import requests
    component = component.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{1,8}', component):
        raise ValueError('Select a valid PDB chemical component before searching.')
    url = f'https://data.rcsb.org/rest/v1/core/chemcomp/{component}'
    try:
        response = requests.get(url, timeout=(15, 15))
        if response.status_code == 404:
            raise ValueError(f'No CCD definition was found for {component}. For custom residue names such as LIG, enter the known ligand SMILES manually.')
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith('No CCD'):
            raise
        raise ValueError('The PDB chemistry service could not be reached. Retry the lookup or enter a verified SMILES manually.') from exc
    descriptors = data.get('rcsb_chem_comp_descriptor', {})
    smiles = descriptors.get('SMILES_stereo') or descriptors.get('SMILES')
    if not smiles:
        candidates = data.get('pdbx_chem_comp_descriptor', [])
        smiles = next((d['descriptor'] for d in candidates if d.get('type') == 'SMILES_CANONICAL'), None)
    if not smiles:
        raise ValueError(f'No SMILES is available for {component}. Enter the exact ligand SMILES manually.')
    comp = data.get('chem_comp', {})
    result = dict(component=component, name=comp.get('name', component), formula=comp.get('formula', ''),
                  formal_charge=comp.get('pdbx_formal_charge'), smiles=smiles, source=url,
                  retrieved_at=datetime.now(timezone.utc).isoformat())
    # Atom names are optional diagnostic data. Their absence does not block lookup.
    try:
        import gemmi
        response = requests.get(f'https://files.rcsb.org/ligands/download/{component}.cif', timeout=(5, 10))
        response.raise_for_status()
        block = gemmi.cif.read_string(response.text).sole_block()
        names = list(block.find_values('_chem_comp_atom.atom_id'))
        elements = list(block.find_values('_chem_comp_atom.type_symbol'))
        result['heavy_atom_names'] = [gemmi.cif.as_string(n) for n,e in zip(names,elements) if e.upper() not in ('H', 'D')]
    except Exception:
        result['heavy_atom_names'] = []
    return result


def ccd_atom_name_check(pdb, definition):
    expected = set(definition.get('heavy_atom_names', []))
    present = {a['name'] for a in atoms(pdb) if a['element'] not in ('H', 'D')}
    overlap = expected & present
    if not expected or len(overlap) < 0.8 * min(len(expected), len(present)):
        return dict(reliable=False, missing=[], extra=[])
    return dict(reliable=True, missing=sorted(expected-present), extra=sorted(present-expected))


def write_ligand(mol, path):
    from rdkit import Chem
    from meeko import MoleculePreparation, PDBQTWriterLegacy
    setups = MoleculePreparation().prepare(mol)
    if len(setups) != 1:
        raise ValueError('This version requires one Meeko ligand setup.')
    pdbqt, ok, error = PDBQTWriterLegacy.write_string(setups[0])
    if not ok:
        raise ValueError(error)
    path = Path(path)
    path.write_text(pdbqt)
    path.with_suffix('.sdf').write_text(Chem.MolToMolBlock(mol) + '\n$$$$\n')


def box(pdb, padding=5.0):
    import numpy as np
    xyz = np.array([a['xyz'] for a in atoms(pdb) if a['element'] not in ('H', 'D')])
    if not len(xyz):
        raise ValueError('Reference contains no heavy atoms.')
    return ((xyz.min(0)+xyz.max(0))/2).tolist(), (xyz.max(0)-xyz.min(0)+2*padding).tolist()


def reference_rmsd(reference, pose):
    """Symmetry-aware heavy-atom RMSD in a fixed receptor frame; never align ligands."""
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign
    ref = Chem.RemoveHs(reference)
    probe = Chem.RemoveHs(pose)
    if ref.GetNumAtoms() != probe.GetNumAtoms():
        raise ValueError('RMSD requires identical heavy-atom counts.')
    if Chem.MolToSmiles(ref, isomericSmiles=True) != Chem.MolToSmiles(probe, isomericSmiles=True):
        raise ValueError('Reference and pose differ in chemical identity or stereochemistry.')
    return float(rdMolAlign.CalcRMS(probe, ref, maxMatches=100000, symmetrizeConjugatedTerminalGroups=True))


def repair_heavy_atoms(pdb):
    from pdbfixer import PDBFixer
    from openmm.app import PDBFile
    fixer = PDBFixer(pdbfile=io.StringIO(pdb))
    fixer.findMissingResidues()
    fixer.missingResidues = {}  # No automatic loop construction.
    fixer.findMissingAtoms()
    fixer.addMissingAtoms(seed=2026)
    out = io.StringIO()
    PDBFile.writeFile(fixer.topology, fixer.positions, out, keepIds=True)
    repaired = out.getvalue()
    # PDBFixer may place a newly generated terminal OXT on the existing O.
    # Correct only newly added OXT atoms; preserve every experimental atom.
    original = {(key(a), a['name']) for a in atoms(pdb)}
    groups = {}
    for atom in atoms(repaired):
        groups.setdefault(key(atom), {})[atom['name']] = atom
    replacements = {}
    import numpy as np
    for ident, group in groups.items():
        if not all(name in group for name in ('CA', 'C', 'O', 'OXT')) or (ident, 'OXT') in original:
            continue
        oxygen, terminal = group['O'], group['OXT']
        carbon = np.array(group['C']['xyz'])
        separation = np.linalg.norm(np.array(oxygen['xyz']) - terminal['xyz'])
        bond_length = np.linalg.norm(np.array(terminal['xyz']) - carbon)
        if separation >= 1.0 and 1.0 <= bond_length <= 1.5:
            continue
        directions = [np.array(group[name]['xyz']) - carbon for name in ('CA', 'O')]
        lengths = [np.linalg.norm(v) for v in directions]
        if min(lengths) < 0.1:
            raise ValueError(f'Cannot reconstruct terminal oxygen geometry for {ident}.')
        direction = -sum(v / length for v, length in zip(directions, lengths))
        norm = np.linalg.norm(direction)
        if norm < 0.1:
            raise ValueError(f'Cannot reconstruct terminal oxygen geometry for {ident}.')
        xyz = carbon + 1.25 * direction / norm
        line = terminal['line']
        replacements[line] = line[:30] + ''.join(f'{v:8.3f}' for v in xyz) + line[54:]
    if replacements:
        repaired = '\n'.join(replacements.get(line, line) for line in repaired.splitlines()) + '\n'
        repaired = 'REMARK 900 CORRECTED OVERLAPPING NEW TERMINAL OXT GEOMETRY\n' + repaired
    return repaired


def prepare_receptor(pdb, directory, template_assignments=''):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    inp = directory / 'receptor_input.pdb'
    inp.write_text(pdb)
    args = [sys.executable, '-m', 'meeko.cli.mk_prepare_receptor', '--read_pdb', str(inp), '-o', str(directory/'receptor'), '-p', '-j', '--write_pdb', str(directory/'receptor_prepared.pdb')]
    if template_assignments.strip():
        args.extend(['--set_template', template_assignments.strip()])
    completed = subprocess.run(args, capture_output=True, text=True, timeout=180)
    log = completed.stdout + '\n' + completed.stderr
    (directory/'preparation.log').write_text(log)
    if completed.returncode != 0 or not (directory/'receptor.pdbqt').exists():
        clean = re.sub(r'\x1b\[[0-9;]*m', '', log)
        residues = list(dict.fromkeys(re.findall(r"(?:key|residue_key)[= :]+['\"]?([A-Za-z0-9_]+:[0-9]+[A-Za-z]?)", clean)))
        problem = 'Invalid inferred bonding or atom valence' if 'valence' in clean.lower() else 'Incomplete or unsupported residue chemistry'
        affected = ', '.join(residues[:12]) or 'see the preparation log'
        raise ValueError(f'{problem}. Affected residues: {affected}. Review coordinates, missing atoms and residue templates. No failed residues were removed. Full diagnostics: {directory / "preparation.log"}')
    return directory/'receptor.pdbqt'


def versions():
    result = {'python': sys.version.split()[0]}
    for name in ('streamlit', 'rdkit', 'meeko', 'vina', 'pdbfixer', 'openmm'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = 'not installed'
    return result


def bundle(directory):
    stream = io.BytesIO()
    root = Path(directory)
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.name != 'worker.pid':
                archive.write(path, path.relative_to(root))
    return stream.getvalue()


def validate_config(config):
    for field in ('center', 'size'):
        values = config[field]
        if len(values) != 3 or not all(math.isfinite(float(v)) for v in values):
            raise ValueError('Box requires three finite coordinates and dimensions.')
    if not all(0 < float(v) <= 60 for v in config['size']):
        raise ValueError('Box dimensions must be greater than zero and at most 60 Å.')
    if not 1 <= int(config['exhaustiveness']) <= 64 or not 1 <= int(config['poses']) <= 20:
        raise ValueError('Docking parameters are outside supported limits.')
    if not 1 <= len(config['seeds']) <= 5 or any(not 1 <= int(v) <= 2147483647 for v in config['seeds']):
        raise ValueError('Use one to five positive 32-bit seeds.')
