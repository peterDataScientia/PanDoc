import json
import os
import sys
import traceback
from pathlib import Path

from .core import reference_rmsd, validate_config


def _prepare_shared_maps(directory, config, cpu_threads):
    """Precompute receptor affinity maps once for the whole job.

    Vina 1.2 supports external map reuse. Computing the maps before loading a
    ligand creates the full atom-type map set, so the same maps can be reused
    across seeds and candidate ligands that share the receptor and grid box.
    """
    from vina import Vina

    prefix = directory / "vina_maps" / "receptor"
    prefix.parent.mkdir(parents=True, exist_ok=True)

    try:
        v = Vina(
            sf_name="vina",
            cpu=max(1, int(cpu_threads)),
            seed=int(config["seeds"][0]),
            verbosity=1,
        )
        v.set_receptor(config["receptor"])
        # No ligand loaded here: Vina computes the full atom-type map set.
        v.compute_vina_maps(
            center=config["center"],
            box_size=config["size"],
            force_even_voxels=True,
        )
        v.write_maps(str(prefix), overwrite=True)
        print("Shared Vina affinity maps prepared once for this job.", flush=True)
        return str(prefix)
    except Exception as exc:
        # Map reuse is a speed optimization only. Docking remains valid if a
        # particular Vina build cannot write/load maps.
        print(f"Shared-map optimization unavailable: {exc}", flush=True)
        return None


def _run_search(directory, config, ligand, seed, cpu_threads, map_prefix=None):
    from rdkit import Chem
    from meeko import PDBQTMolecule, RDKitMolCreate
    from vina import Vina

    directory = Path(directory)
    cpu_threads = max(1, int(cpu_threads))

    print(f"Starting docking: {ligand['name']} / seed {seed}", flush=True)
    print(f"AutoDock Vina CPU threads: {cpu_threads}", flush=True)

    vina = Vina(
        sf_name="vina",
        cpu=cpu_threads,
        seed=int(seed),
        verbosity=2,
    )
    vina.set_receptor(config["receptor"])
    vina.set_ligand_from_file(ligand["path"])

    if map_prefix:
        try:
            vina.load_maps(map_prefix)
            print("Loaded shared Vina affinity maps.", flush=True)
        except Exception as exc:
            print(f"Shared maps could not be loaded; recomputing maps: {exc}", flush=True)
            vina.compute_vina_maps(center=config["center"], box_size=config["size"])
    else:
        vina.compute_vina_maps(center=config["center"], box_size=config["size"])

    print(f"=== AutoDock Vina live output · {ligand['name']} · seed {seed} ===", flush=True)
    print(vina, flush=True)
    vina.dock(
        exhaustiveness=config["exhaustiveness"],
        n_poses=config["poses"],
    )

    stem = directory / f"{ligand['id']}_seed{seed}"
    output = str(stem.with_suffix(".pdbqt"))
    vina.write_poses(output, n_poses=config["poses"], overwrite=True)
    scores = vina.energies(n_poses=config["poses"])

    poses = RDKitMolCreate.from_pdbqt_mol(
        PDBQTMolecule.from_file(output, skip_typing=True)
    )
    if len(poses) != 1 or poses[0] is None:
        raise ValueError("Meeko could not reconstruct the docked molecule.")

    mol = poses[0]
    reference = None
    if config.get("reference"):
        reference = next(iter(Chem.SDMolSupplier(config["reference"], removeHs=False)))
        if reference is None:
            raise ValueError("Unreadable reference SDF.")

    writer = Chem.SDWriter(str(stem.with_suffix(".sdf")))
    rows = []
    for rank, conf in enumerate(mol.GetConformers(), 1):
        pose = Chem.Mol(mol)
        pose.RemoveAllConformers()
        pose.AddConformer(Chem.Conformer(conf), assignId=True)
        score = float(scores[rank - 1][0])
        pose.SetProp("vina_score_kcal_mol", str(score))
        row = dict(
            ligand=ligand["name"],
            ligand_id=ligand["id"],
            seed=seed,
            rank=rank,
            score_kcal_mol=score,
            sdf=stem.with_suffix(".sdf").name,
        )
        if reference is not None:
            row["reference_rmsd_A"] = reference_rmsd(reference, pose)
            pose.SetProp("reference_rmsd_A", str(row["reference_rmsd_A"]))
        writer.write(pose)
        rows.append(row)
    writer.close()

    print(f"Completed docking: {ligand['name']} / seed {seed}", flush=True)
    return rows


def run(directory):
    directory = Path(directory)
    config = json.loads((directory / "config.json").read_text())
    validate_config(config)

    results = []
    completed_searches = 0

    def update(state, **extra):
        tmp = directory / "status.tmp"
        tmp.write_text(json.dumps({
            "state": state,
            "completed": completed_searches,
            "poses_written": len(results),
            **extra,
        }))
        tmp.replace(directory / "status.json")

    try:
        tasks = [
            (ligand, int(seed))
            for ligand in config["ligands"]
            for seed in config["seeds"]
        ]
        total = len(tasks)
        update("running", total=total)

        available_cpu = max(1, int(config.get("cpu", os.cpu_count() or 1)))
        exhaustiveness = max(1, int(config["exhaustiveness"]))

        # Vina itself parallelizes its independent Monte-Carlo runs. Giving a
        # single search all useful CPU threads avoids competing Vina processes,
        # duplicated search overhead, and idle remainder cores.
        vina_threads = max(1, min(available_cpu, exhaustiveness))

        print(
            f"Computer B performance mode: {available_cpu} CPU(s) available; "
            f"Vina uses {vina_threads} thread(s) per search; "
            f"exhaustiveness={exhaustiveness}.",
            flush=True,
        )
        print(
            "Searches run one at a time because Vina parallelizes the "
            "exhaustiveness runs internally.",
            flush=True,
        )

        map_prefix = _prepare_shared_maps(directory, config, vina_threads)

        for ligand, seed in tasks:
            if (directory / "cancel.request").exists():
                update("cancelled", total=total)
                return

            update(
                "running",
                total=total,
                current=f"{ligand['name']} / seed {seed}",
                cpu_threads=vina_threads,
            )

            rows = _run_search(
                str(directory),
                config,
                ligand,
                seed,
                vina_threads,
                map_prefix=map_prefix,
            )
            results.extend(rows)
            completed_searches += 1

            (directory / "results.json").write_text(json.dumps(results, indent=2))
            update(
                "running",
                total=total,
                current=f"{completed_searches}/{total} docking searches completed",
                cpu_threads=vina_threads,
            )
            print(f"Completed docking search {completed_searches}/{total}", flush=True)

        update("completed", total=total, cpu_threads=vina_threads)

    except Exception as exc:
        traceback.print_exc()
        update("failed", error=str(exc))


if __name__ == "__main__":
    run(sys.argv[1])
