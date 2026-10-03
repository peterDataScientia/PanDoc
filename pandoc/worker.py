import json
import sys
import traceback
from pathlib import Path

from .core import reference_rmsd, validate_config


def run(directory):
    from rdkit import Chem
    from meeko import PDBQTMolecule, RDKitMolCreate
    from vina import Vina
    directory = Path(directory)
    config = json.loads((directory/'config.json').read_text())
    validate_config(config)
    results = []
    completed_searches = 0

    def update(state, **extra):
        tmp = directory/'status.tmp'
        tmp.write_text(json.dumps({'state': state, 'completed': completed_searches, 'poses_written': len(results), **extra}))
        tmp.replace(directory/'status.json')

    try:
        total = len(config['ligands'])*len(config['seeds'])
        update('running', total=total)
        reference = None
        if config.get('reference'):
            reference = next(iter(Chem.SDMolSupplier(config['reference'], removeHs=False)))
            if reference is None:
                raise ValueError('Unreadable reference SDF.')
        for ligand in config['ligands']:
            for seed in config['seeds']:
                if (directory/'cancel.request').exists():
                    update('cancelled', total=total)
                    return
                update('running', total=total, current=f"{ligand['name']} / seed {seed}")
                print(f"Starting docking: {ligand['name']} / seed {seed}", flush=True)
                vina = Vina(sf_name='vina', cpu=config.get('cpu', 2), seed=int(seed))
                vina.set_receptor(config['receptor'])
                vina.set_ligand_from_file(ligand['path'])
                vina.compute_vina_maps(center=config['center'], box_size=config['size'])
                vina.dock(exhaustiveness=config['exhaustiveness'], n_poses=config['poses'])
                stem = directory/f"{ligand['id']}_seed{seed}"
                output = str(stem.with_suffix('.pdbqt'))
                vina.write_poses(output, n_poses=config['poses'], overwrite=True)
                scores = vina.energies(n_poses=config['poses'])
                poses = RDKitMolCreate.from_pdbqt_mol(PDBQTMolecule.from_file(output, skip_typing=True))
                if len(poses) != 1 or poses[0] is None:
                    raise ValueError('Meeko could not reconstruct the docked molecule.')
                mol = poses[0]
                writer = Chem.SDWriter(str(stem.with_suffix('.sdf')))
                for rank, conf in enumerate(mol.GetConformers(), 1):
                    pose = Chem.Mol(mol)
                    pose.RemoveAllConformers()
                    pose.AddConformer(Chem.Conformer(conf), assignId=True)
                    score = float(scores[rank-1][0])
                    pose.SetProp('vina_score_kcal_mol', str(score))
                    row = dict(ligand=ligand['name'], ligand_id=ligand['id'], seed=seed, rank=rank, score_kcal_mol=score, sdf=stem.with_suffix('.sdf').name)
                    if reference is not None:
                        row['reference_rmsd_A'] = reference_rmsd(reference, pose)
                        pose.SetProp('reference_rmsd_A', str(row['reference_rmsd_A']))
                    writer.write(pose)
                    results.append(row)
                writer.close()
                completed_searches += 1
                print(f"Completed docking search {completed_searches}/{total}", flush=True)
                (directory/'results.json').write_text(json.dumps(results, indent=2))
                update('running', total=total, current=f"{ligand['name']} / seed {seed}")
        update('completed', total=total)
    except Exception as exc:
        traceback.print_exc()
        update('failed', error=str(exc))


if __name__ == '__main__':
    run(sys.argv[1])
