"""Conservative coordinate checks and auditable preparation changes."""
from collections import Counter
import math
from . import core

# Standard residue identities, rather than atom-count estimates.
SIDECHAINS = dict(ALA='CB', ARG='CB CG CD NE CZ NH1 NH2', ASN='CB CG OD1 ND2',
 ASP='CB CG OD1 OD2', CYS='CB SG', GLN='CB CG CD OE1 NE2', GLU='CB CG CD OE1 OE2',
 GLY='', HIS='CB CG ND1 CD2 CE1 NE2', ILE='CB CG1 CG2 CD1', LEU='CB CG CD1 CD2',
 LYS='CB CG CD CE NZ', MET='CB CG SD CE', PHE='CB CG CD1 CD2 CE1 CE2 CZ', PRO='CB CG CD',
 SER='CB OG', THR='CB OG1 CG2', TRP='CB CG CD1 CD2 NE1 CE2 CE3 CZ2 CZ3 CH2',
 TYR='CB CG CD1 CD2 CE1 CE2 CZ OH', VAL='CB CG1 CG2')
ALIASES = dict(HID='HIS', HIE='HIS', HIP='HIS', ASH='ASP', GLH='GLU', CYX='CYS', LYN='LYS')
METALS = {'ZN','FE','MG','MN','CA','CU','CO','NI','CD','NA','K'}


def alternate_options(pdb):
    groups = {}
    for a in core.atoms(pdb):
        groups.setdefault(core.key(a), []).append(a)
    result = []
    for ident, aa in groups.items():
        labels = sorted({a['alt'] for a in aa if a['alt']})
        if not labels:
            continue
        options = []
        for label in labels:
            variant = [a for a in aa if not a['alt'] or a['alt'] == label]
            heavy = {a['name'] for a in variant if a['element'] not in ('H','D')}
            labelled = [a for a in aa if a['alt'] == label]
            occupancy = sum(a['occupancy'] for a in labelled)/len(labelled)
            options.append(dict(label=label, mean_occupancy=round(occupancy,3), heavy_atoms=len(heavy)))
        # Complete alternatives take precedence; occupancy then breaks ties.
        best = sorted(options, key=lambda r: (-r['heavy_atoms'], -r['mean_occupancy'], r['label']))[0]
        result.append(dict(residue=ident, options=options, recommended=best['label']))
    return result


def inventory(pdb):
    rows = core.inspect(pdb)
    for row in rows:
        if row['kind'] == 'Other component' and row['name'] in METALS:
            row['kind'] = 'Metal / ion'
    links = [line for line in pdb.splitlines() if line.startswith(('LINK  ', 'SSBOND'))]
    return dict(components=rows, alternates=alternate_options(pdb), connections=links,
                connection_scope='Reported LINK and SSBOND records; absence does not establish absence of covalent or metal connections.')



def check(pdb, raw=False):
    atoms = core.atoms(pdb)
    heavy = [a for a in atoms if a['element'] not in ('H', 'D')]
    groups = {}
    issues = []
    def add(severity, residue, problem, action):
        issues.append(dict(severity=severity, residue=residue, problem=problem, action=action))
    for atom in heavy:
        groups.setdefault(core.key(atom), []).append(atom)
        if not all(math.isfinite(v) for v in atom['xyz']):
            add('Error', core.key(atom), 'Nonfinite coordinates', 'Upload corrected coordinates.')
    for ident, aa in groups.items():
        counts = Counter(a['name'] for a in aa)
        duplicates = [name for name, count in counts.items() if count > 1]
        if duplicates and (not raw or any(sum(a['name']==name and a['alt']==label for a in aa)>1 for name in duplicates for label in {a['alt'] for a in aa})):
            add('Error', ident, 'Duplicate atom names: '+', '.join(duplicates), 'Select one consistent alternate conformation or correct duplicate records.')
        if any(a['alt'] for a in aa):
            add('Warning', ident, 'Alternate conformations remain', 'Select one alternate conformation in Load complex.')
        residue_type = ALIASES.get(aa[0]['res'], aa[0]['res'])
        if residue_type in SIDECHAINS:
            required = set(('N CA C O '+SIDECHAINS[residue_type]).split())
            missing = sorted(required-set(counts))
            unexpected = sorted(set(counts)-required-{'OXT'})
            if missing:
                add('Warning', ident, 'Missing heavy atoms: '+', '.join(missing), 'Enable heavy-atom reconstruction; inspect the generated atoms. Missing whole residues require separate modelling.')
            if unexpected:
                add('Warning', ident, 'Unexpected heavy atom names: '+', '.join(unexpected), 'Review naming and residue chemistry before template assignment.')
        if any(a['occupancy'] <= 0 for a in aa):
            add('Warning', ident, 'Zero or negative occupancy', 'Review experimental support before using these coordinates.')
        if aa[0]['res'] not in core.STANDARD:
            add('Warning', ident, ('Metal / ion requires review: ' if aa[0]['res'] in METALS else 'Water retention requires review: ' if aa[0]['res'] in core.WATERS else 'Retained nonstandard component: ')+aa[0]['res'], 'Review component chemistry and Meeko template support; metals and covalent components may need curated preparation.')
        named = {a['name']: a for a in aa}
        if all(n in named for n in ('C', 'O', 'OXT')):
            distance = math.dist(named['O']['xyz'], named['OXT']['xyz'])
            bond = math.dist(named['C']['xyz'], named['OXT']['xyz'])
            if distance < 1 or not 1 <= bond <= 1.5:
                add('Error', ident, 'Invalid terminal oxygen geometry', 'Correct terminal OXT geometry; existing input atoms require a curated correction.')
    finite = [a for a in heavy if all(math.isfinite(v) for v in a['xyz'])]
    if finite:
        from scipy.spatial import cKDTree
        for i, j in sorted(cKDTree([a['xyz'] for a in finite]).query_pairs(0.65)):
            a,b = finite[i], finite[j]
            if raw and core.key(a)==core.key(b) and a['alt'] and b['alt'] and a['alt'] != b['alt']:
                continue
            add('Error', core.key(a), f"Severe overlap: {a['name']} / {core.key(b)} {b['name']} ({math.dist(a['xyz'], b['xyz']):.2f} Å)", 'Inspect coordinates or alternate conformations; do not delete the residue automatically.')
    for line in pdb.splitlines():
        if line.startswith(('LINK  ', 'SSBOND')):
            add('Warning', 'Structure', 'Explicit connection record: '+line[:6].strip(), 'Review linked residues and supported chemistry; preserve the intended connection during preparation.')
    previous = {}
    for ident, aa in groups.items():
        if aa[0]['res'] not in core.STANDARD:
            continue
        named = {a['name']: a for a in aa}
        chain = aa[0]['chain']
        if chain in previous:
            prev_id, prev = previous[chain]
            if 'C' in prev and 'N' in named:
                d = math.dist(prev['C']['xyz'], named['N']['xyz'])
                if d > 2:
                    add('Warning', ident, f'Possible chain break after {prev_id} ({d:.2f} Å)', 'Review missing sequence or intentional chain breaks; loops are not built automatically.')
        previous[chain] = (ident, named)
    return dict(atom_count=len(atoms), heavy_atom_count=len(heavy), residue_count=len(groups), issues=issues,
                scope='Coordinate screening only; Meeko performs residue-template and valence checks. Protonation and full steric validation require scientific review.')


def changes(before, after):
    def mapping(pdb):
        result = {}
        for a in core.atoms(pdb):
            if a['element'] not in ('H','D'):
                result.setdefault((core.key(a), a['name']), []).append(a)
        return result
    original, prepared = mapping(before), mapping(after)
    rows = []
    for ident, name in sorted(prepared.keys()-original.keys()):
        rows.append(dict(residue=ident, atom=name, change='Added heavy atom', displacement_A=None))
    for ident, name in sorted(original.keys()-prepared.keys()):
        rows.append(dict(residue=ident, atom=name, change='Removed or renamed heavy atom', displacement_A=None))
    for k in sorted(original.keys() & prepared.keys()):
        d=min(math.dist(a['xyz'], b['xyz']) for a in original[k] for b in prepared[k])
        if d > 0.002:
            rows.append(dict(residue=k[0], atom=k[1], change='Moved heavy atom', displacement_A=round(d,4)))
    return rows
