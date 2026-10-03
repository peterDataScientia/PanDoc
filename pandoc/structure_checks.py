"""Conservative coordinate checks and auditable preparation changes."""
from collections import Counter
import math
from . import core


def check(pdb):
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
        if duplicates:
            add('Error', ident, 'Duplicate atom names: '+', '.join(duplicates), 'Select one consistent alternate conformation or correct duplicate records.')
        if any(a['alt'] for a in aa):
            add('Warning', ident, 'Alternate conformations remain', 'Select one alternate conformation in Load complex.')
        expected = core.EXPECTED.get(aa[0]['res'])
        if expected and len(counts) < expected:
            add('Warning', ident, f'Potential missing heavy atoms: {expected-len(counts)}', 'Enable heavy-atom reconstruction; inspect the generated atoms.')
        if aa[0]['res'] not in core.STANDARD:
            add('Warning', ident, 'Retained nonstandard component: '+aa[0]['res'], 'Review component chemistry and Meeko template support; metals and covalent components may need curated preparation.')
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
            add('Error', core.key(a), f"Severe overlap: {a['name']} / {core.key(b)} {b['name']} ({math.dist(a['xyz'], b['xyz']):.2f} Å)", 'Inspect coordinates or alternate conformations; do not delete the residue automatically.')
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
        return {(core.key(a), a['name']): a for a in core.atoms(pdb) if a['element'] not in ('H','D')}
    original, prepared = mapping(before), mapping(after)
    rows = []
    for ident, name in sorted(prepared.keys()-original.keys()):
        rows.append(dict(residue=ident, atom=name, change='Added heavy atom', displacement_A=None))
    for ident, name in sorted(original.keys()-prepared.keys()):
        rows.append(dict(residue=ident, atom=name, change='Removed or renamed heavy atom', displacement_A=None))
    for k in sorted(original.keys() & prepared.keys()):
        d=math.dist(original[k]['xyz'], prepared[k]['xyz'])
        if d > 0.002:
            rows.append(dict(residue=k[0], atom=k[1], change='Moved heavy atom', displacement_A=round(d,4)))
    return rows
