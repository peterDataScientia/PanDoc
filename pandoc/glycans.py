"""Reconstruct multi-residue glycan chemistry using deposited CCD bonds and PDB LINKs.

No inter-residue bonds are invented from distance; failures remain explicit.
"""
from collections import Counter

def _key(chain, seq, insertion, residue):
    return f"{chain or '_'}:{seq}{insertion}:{residue}"

def _endpoints(line):
    return (
        (_key(line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip()), line[12:16].strip()),
        (_key(line[51:52].strip(), line[52:56].strip(), line[56:57].strip(), line[47:50].strip()), line[42:46].strip()),
    )

def _ccd(component):
    import gemmi
    import requests
    response = requests.get(f'https://files.rcsb.org/ligands/download/{component}.cif', timeout=(10, 20))
    response.raise_for_status()
    block = gemmi.cif.read_string(response.text).sole_block()
    names = block.find_values('_chem_comp_atom.atom_id')
    elems = block.find_values('_chem_comp_atom.type_symbol')
    charges = block.find_values('_chem_comp_atom.charge')
    atoms = {str(n): (str(e).upper(), int(c) if c not in ('.', '?', '') else 0)
             for n, e, c in zip(names, elems, charges) if str(e).upper() not in ('H', 'D')}
    bonds = list(zip(block.find_values('_chem_comp_bond.atom_id_1'),
                     block.find_values('_chem_comp_bond.atom_id_2'),
                     block.find_values('_chem_comp_bond.value_order')))
    if not atoms or not bonds:
        raise ValueError(f'CCD {component} has no complete atom/bond definition.')
    return atoms, bonds

def reconstruct_glycan(reference_pdb, original_pdb, ccd_loader=None):
    """Return (RDKit ligand with crystal coords, audit). Requires explicit PDB LINKs.

    Stereochemistry is inferred from observed coordinates, NOT certified against
    the full glycan's stereochemical reference; human inspection is required.
    """
    from rdkit import Chem
    from rdkit.Geometry import Point3D
    from . import core
    if not original_pdb:
        raise ValueError('Original PDB with deposited LINK records is required.')
    rows = [a for a in core.atoms(reference_pdb) if a['element'] not in ('H', 'D')]
    residues = sorted({core.key(a) for a in rows})
    if len(residues) < 2:
        raise ValueError('Select all residues of the complete glycan.')
    by_name = {(core.key(a), a['name']): a for a in rows}
    if len(by_name) != len(rows):
        raise ValueError('Duplicate atom names; resolve alternate conformations.')
    loader = ccd_loader or _ccd
    definitions = {}
    missing = {}
    for res in residues:
        aa = [a for a in rows if core.key(a) == res]
        component = aa[0]['res']
        if component not in definitions:
            definitions[component] = loader(component)
        defs, _ = definitions[component]
        observed = {a['name'] for a in aa}
        if observed - defs.keys():
            raise ValueError(f'{res}: unknown CCD atoms: {sorted(observed - defs.keys())}')
        for a in aa:
            if a['element'] != defs[a['name']][0]:
                raise ValueError(f'{res}:{a["name"]} element mismatch against CCD.')
        missing[res] = sorted(defs.keys() - observed)

    link_bonds, seen = [], set()
    for line in original_pdb.splitlines():
        if not line.startswith('LINK  '):
            continue
        p, q = _endpoints(line)
        pair = frozenset((p, q))
        if p in by_name and q in by_name and p[0] != q[0] and pair not in seen:
            link_bonds.append((p, q))
            seen.add(pair)
    if len(link_bonds) < len(residues) - 1:
        raise ValueError(f'{len(link_bonds)} inter-residue LINK records for '
                         f'{len(residues)} residues. Connectivity is incomplete; no inferred bonds added.')
    allowed_missing = {}
    for res, absent in missing.items():
        if absent:
            # An anomeric leaving oxygen is omitted only when the deposited
            # LINK explicitly replaces it by bonding that very C1 center.
            linked_anomeric_carbon = any(
                endpoint[0] == res and endpoint[1] == 'C1'
                for pair in link_bonds for endpoint in pair
            )
            if absent != ['O1'] or not linked_anomeric_carbon:
                raise ValueError(
                    f'{res}: missing CCD atoms {absent}; no C1 LINK explains O1. '
                    'Do not fabricate or discard heavy atoms.')
            allowed_missing[res] = absent

    mol = Chem.RWMol()
    conf = Chem.Conformer(len(rows))
    index = {}
    for a in rows:
        element, charge = definitions[a['res']][0][a['name']]
        atom = Chem.Atom(element.capitalize())
        atom.SetFormalCharge(charge)
        atom.SetProp('_TriposAtomName', a['name'])
        atom.SetProp('pdb_residue_key', core.key(a))
        idx = mol.AddAtom(atom)
        index[(core.key(a), a['name'])] = idx
        conf.SetAtomPosition(idx, Point3D(*a['xyz']))
    bond_types = dict(SING=Chem.BondType.SINGLE, DOUB=Chem.BondType.DOUBLE,
                      TRIP=Chem.BondType.TRIPLE, AROM=Chem.BondType.AROMATIC)
    for res in residues:
        component = res.rsplit(':', 1)[-1]
        for n1, n2, typ in definitions[component][1]:
            p, q = (res, str(n1)), (res, str(n2))
            if p in index and q in index and mol.GetBondBetweenAtoms(index[p], index[q]) is None:
                mol.AddBond(index[p], index[q], bond_types.get(str(typ).upper(), Chem.BondType.SINGLE))
    for p, q in link_bonds:
        i, j = index[p], index[q]
        if mol.GetBondBetweenAtoms(i, j) is None:
            mol.AddBond(i, j, Chem.BondType.SINGLE)
    result = mol.GetMol()
    result.AddConformer(conf, assignId=True)
    if len(Chem.GetMolFrags(result)) != 1:
        raise ValueError('CCD+LINK reconstructed glycan is disconnected.')
    try:
        Chem.SanitizeMol(result)
        Chem.AssignAtomChiralTagsFromStructure(result, replaceExistingTags=True)
        Chem.AssignStereochemistry(result, force=True, cleanIt=True)
    except Exception as exc:
        raise ValueError(f'RDKit valence or stereochemistry failed: {exc}') from exc
    audit = dict(residues=residues, heavy_atoms=len(rows), links=len(link_bonds),
                 missing_ccd_atoms=allowed_missing, formal_charge=Chem.GetFormalCharge(result),
                 proposed_isomeric_smiles=Chem.MolToSmiles(result, isomericSmiles=True),
                 note='CCD+deposited LINK reconstruction; coordinates conserved; stereochemistry/charge require independent confirmation.')
    return Chem.AddHs(result, addCoords=True), audit
