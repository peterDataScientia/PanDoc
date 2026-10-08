import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .core import reference_rmsd, validate_config


def _run_search(directory_str, config, ligand, seed, cpu_threads):
    from rdkit import Chem
    from meeko import PDBQTMolecule, RDKitMolCreate
    from vina import Vina

    directory = Path(directory_str)
    print(f"Starting docking: {ligand['name']} / seed {seed}", flush=True)
    print(f"AutoDock Vina CPU threads: {cpu_threads}", flush=True)

    vina = Vina(
        sf_name='vina',
        cpu=max(1, int(cpu_threads)),
        seed=int(seed),
        verbosity=2,
    )
    vina.set_receptor(config['receptor'])
    vina.set_ligand_from_file(ligand['path'])
    print(f"=== AutoDock Vina live output · {ligand['name']} · seed {seed} ===", flush=True)
    print(vina, flush=True)
    vina.compute_vina_maps(center=config['center'], box_size=config['size'])
    vina.dock(exhaustiveness=config['exhaustiveness'], n_poses=config['poses'])

    stem = directory / f"{ligand['id']}_seed{seed}"
    output = str(stem.with_suffix('.pdbqt'))
    vina.write_poses(output, n_poses=config['poses'], overwrite=True)
    scores = vina.energies(n_poses=config['poses'])

    poses = RDKitMolCreate.from_pdbqt_mol(
        PDBQTMolecule.from_file(output, skip_typing=True)
    )
    if len(poses) != 1 or poses[0] is None:
        raise ValueError('Meeko could not reconstruct the docked molecule.')

    mol = poses[0]
    reference = None
    if config.get('reference'):
        reference = next(iter(Chem.SDMolSupplier(config['reference'], removeHs=False)))
        if reference is None:
            raise ValueError('Unreadable reference SDF.')

    writer = Chem.SDWriter(str(stem.with_suffix('.sdf')))
    rows = []
    for rank, conf in enumerate(mol.GetConformers(), 1):
        pose = Chem.Mol(mol)
        pose.RemoveAllConformers()
        pose.AddConformer(Chem.Conformer(conf), assignId=True)
        score = float(scores[rank - 1][0])
        pose.SetProp('vina_score_kcal_mol', str(score))
        row = dict(
            ligand=ligand['name'],
            ligand_id=ligand['id'],
            seed=seed,
            rank=rank,
            score_kcal_mol=score,
            sdf=stem.with_suffix('.sdf').name,
        )
        if reference is not None:
            row['reference_rmsd_A'] = reference_rmsd(reference, pose)
            pose.SetProp('reference_rmsd_A', str(row['reference_rmsd_A']))
        writer.write(pose)
        rows.append(row)
    writer.close()

    print(f"Completed docking: {ligand['name']} / seed {seed}", flush=True)
    return rows


def run(directory):
    directory = Path(directory)
    config = json.loads((directory/'config.json').read_text())
    validate_config(config)
    results = []
    completed_searches = 0

    def update(state, **extra):
        tmp = directory/'status.tmp'
        tmp.write_text(json.dumps({
            'state': state,
            'completed': completed_searches,
            'poses_written': len(results),
            **extra,
        }))
        tmp.replace(directory/'status.json')

    try:
        tasks = [
            (ligand, int(seed))
            for ligand in config['ligands']
            for seed in config['seeds']
        ]
        total = len(tasks)
        update('running', total=total)

        total_cpu = max(1, int(config.get('cpu', 2)))
        parallel = bool(config.get('parallel_searches', False)) and total > 1

        if parallel:
            max_workers = min(total, total_cpu)
            cpu_per_search = max(1, total_cpu // max_workers)
            print(
                f"Parallel docking enabled: {max_workers} searches concurrently "
                f"with {cpu_per_search} Vina CPU thread(s) each.",
                flush=True,
            )
            update(
                'running',
                total=total,
                current=f'{max_workers} docking searches running in parallel',
                parallel_workers=max_workers,
                cpu_per_search=cpu_per_search,
            )

            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        _run_search,
                        str(directory),
                        config,
                        ligand,
                        seed,
                        cpu_per_search,
                    ): (ligand, seed)
                    for ligand, seed in tasks
                }
                for future in as_completed(futures):
                    ligand, seed = futures[future]
                    if (directory/'cancel.request').exists():
                        for pending in futures:
                            pending.cancel()
                        update('cancelled', total=total)
                        return
                    rows = future.result()
                    results.extend(rows)
                    completed_searches += 1
                    (directory/'results.json').write_text(json.dumps(results, indent=2))
                    update(
                        'running',
                        total=total,
                        current=f'{completed_searches}/{total} parallel searches completed',
                        parallel_workers=max_workers,
                        cpu_per_search=cpu_per_search,
                    )
                    print(
                        f"Completed docking search {completed_searches}/{total} "
                        f"({ligand['name']} / seed {seed})",
                        flush=True,
                    )
        else:
            for ligand, seed in tasks:
                if (directory/'cancel.request').exists():
                    update('cancelled', total=total)
                    return
                update('running', total=total, current=f"{ligand['name']} / seed {seed}")
                rows = _run_search(
                    str(directory),
                    config,
                    ligand,
                    seed,
                    total_cpu,
                )
                results.extend(rows)
                completed_searches += 1
                (directory/'results.json').write_text(json.dumps(results, indent=2))
                update('running', total=total, current=f"{ligand['name']} / seed {seed}")
                print(f"Completed docking search {completed_searches}/{total}", flush=True)

        update('completed', total=total)
    except Exception as exc:
        traceback.print_exc()
        update('failed', error=str(exc))


if __name__ == '__main__':
    run(sys.argv[1])
