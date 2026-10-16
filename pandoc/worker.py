import json
import os
import sys
import traceback
from pathlib import Path

from .core import reference_rmsd, validate_config


def format_vina_affinities(energies, ligand_name, seed):
    """Format the energies returned by the SAME Vina search; never estimate scores."""
    scored = [(rank, float(energy[0])) for rank, energy in enumerate(energies, 1)]
    if not scored:
        return f"Vina returned no pose affinities for {ligand_name} / seed {seed}."
    best_rank, best_score = min(scored, key=lambda item: item[1])
    lines = [
        f"=== Vina docking scores | {ligand_name} | seed {seed} ===",
        "Mode    Affinity (kcal/mol)",
        *[f"{rank:>4}    {score:>10.3f}" for rank, score in scored],
        f"Best affinity: {best_score:.3f} kcal/mol (mode {best_rank})",
    ]
    return "\n".join(lines)


def _run_search(directory, config, ligand, seed, cpu_threads, stage_callback=None):
    from rdkit import Chem
    from meeko import PDBQTMolecule, RDKitMolCreate
    from vina import Vina

    directory = Path(directory)
    cpu_threads = max(1, int(cpu_threads))

    def checkpoint(stage, message, **progress):
        """Record the exact step before starting a potentially costly operation."""
        print(message, flush=True)
        if stage_callback is not None:
            stage_callback(stage, **progress)

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

    # Compute the affinity maps in the same Vina object used for docking.
    # Reloading serialized shared maps caused a reproducible native Vina crash
    # in our real docking smoke test, even though the map files loaded cleanly.
    checkpoint("preparing_vina_maps", "Computing Vina affinity maps for this search...")
    vina.compute_vina_maps(center=config["center"], box_size=config["size"])

    print(f"=== AutoDock Vina live output · {ligand['name']} · seed {seed} ===", flush=True)
    print(vina, flush=True)
    checkpoint("vina_search", "Performing Vina search...")
    vina.dock(
        exhaustiveness=config["exhaustiveness"],
        n_poses=config["poses"],
    )

    stem = directory / f"{ligand['id']}_seed{seed}"
    output = str(stem.with_suffix(".pdbqt"))
    checkpoint("writing_poses", "Vina search returned; exporting docked PDBQT poses...")
    vina.write_poses(output, n_poses=config["poses"], overwrite=True)
    checkpoint("reading_scores", "PDBQT poses saved; retrieving Vina scores...")
    scores = vina.energies(n_poses=config["poses"])
    # Vina's native Python binding does not reliably print the final affinity
    # table to captured stdout. Publish the actual returned energies immediately,
    # before optional RDKit/Meeko pose reconstruction and RMSD calculations.
    print(format_vina_affinities(scores, ligand["name"], seed), flush=True)

    checkpoint("reconstructing_poses", "Scores retrieved; reconstructing RDKit conformers via Meeko...")
    poses = RDKitMolCreate.from_pdbqt_mol(
        PDBQTMolecule.from_file(output, skip_typing=True)
    )
    if len(poses) != 1 or poses[0] is None:
        raise ValueError("Meeko could not reconstruct the docked molecule.")

    mol = poses[0]
    reference = None
    if config.get("reference"):
        checkpoint("loading_reference", "Loading crystallographic reference for fixed-frame RMSD...")
        reference = next(iter(Chem.SDMolSupplier(config["reference"], removeHs=False)))
        if reference is None:
            raise ValueError("Unreadable reference SDF.")

    writer = Chem.SDWriter(str(stem.with_suffix(".sdf")))
    rows = []
    conformers = list(mol.GetConformers())
    try:
        for rank, conf in enumerate(conformers, 1):
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
                checkpoint(
                    "computing_rmsd",
                    f"Calculating symmetry-aware RMSD for pose {rank}/{len(conformers)}...",
                    pose_rank=rank,
                    poses_total=len(conformers),
                )
                row["reference_rmsd_A"] = reference_rmsd(reference, pose)
                pose.SetProp("reference_rmsd_A", str(row["reference_rmsd_A"]))
                print(
                    f"Pose {rank}: affinity {score:.3f} kcal/mol · "
                    f"reference RMSD {row['reference_rmsd_A']:.3f} Å",
                    flush=True,
                )
            checkpoint(
                "writing_sdf",
                f"Writing SDF pose {rank}/{len(conformers)}...",
                pose_rank=rank,
                poses_total=len(conformers),
            )
            writer.write(pose)
            rows.append(row)
    finally:
        writer.close()

    checkpoint("search_completed", f"Saved {len(rows)} SDF poses and scores.")
    print(f"Completed docking: {ligand['name']} / seed {seed}", flush=True)
    return rows


def run(directory):
    directory = Path(directory)
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
        config = json.loads((directory / "config.json").read_text())
        validate_config(config)
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
            f"Vina compute: {available_cpu} CPU(s) allocated; "
            f"Vina uses {vina_threads} thread(s) per search; "
            f"exhaustiveness={exhaustiveness}.",
            flush=True,
        )
        print(
            "Searches run one at a time because Vina parallelizes the "
            "exhaustiveness runs internally.",
            flush=True,
        )

        print(
            "Per-search Vina affinity maps enabled; serialized shared-map reuse "
            "disabled to avoid native docking crashes.",
            flush=True,
        )

        for ligand, seed in tasks:
            if (directory / "cancel.request").exists():
                update("cancelled", total=total)
                return

            update(
                "running",
                total=total,
                current=f"{ligand['name']} / seed {seed}",
                cpu_threads=vina_threads,
                stage="starting_search",
            )

            def report_stage(stage, **progress):
                update(
                    "running",
                    total=total,
                    current=f"{ligand['name']} / seed {seed}",
                    cpu_threads=vina_threads,
                    stage=stage,
                    **progress,
                )

            rows = _run_search(
                str(directory),
                config,
                ligand,
                seed,
                vina_threads,
                stage_callback=report_stage,
            )
            results.extend(rows)
            completed_searches += 1
            if rows:
                best = min(rows, key=lambda row: row["score_kcal_mol"])
                print(
                    f"Completed search {completed_searches}/{total} | "
                    f"{ligand['name']} | seed {seed} | "
                    f"best affinity {best['score_kcal_mol']:.3f} kcal/mol",
                    flush=True,
                )

            (directory / "results.json").write_text(json.dumps(results, indent=2))
            update(
                "running",
                total=total,
                current=f"{completed_searches}/{total} docking searches completed",
                cpu_threads=vina_threads,
            )
            print(f"Completed docking search {completed_searches}/{total}", flush=True)

        if results:
            best = min(results, key=lambda row: row["score_kcal_mol"])
            print(
                f"Best docking score across all searches: "
                f"{best['score_kcal_mol']:.3f} kcal/mol | "
                f"{best['ligand']} | seed {best['seed']} | pose {best['rank']}",
                flush=True,
            )
        update("completed", total=total, stage="completed", cpu_threads=vina_threads)

    except Exception as exc:
        traceback.print_exc()
        update("failed", error=str(exc), stage="failed")


if __name__ == "__main__":
    run(sys.argv[1])
