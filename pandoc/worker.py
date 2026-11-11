import json
import os
import sys
import traceback
from pathlib import Path

from .core import reference_rmsd, validate_config



def allocated_cpu_count() -> int:
    """Logical CPUs available to this worker, honoring Linux affinity."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, int(os.cpu_count() or 1))


def vina_cpu_threads(config: dict) -> tuple[int, int]:
    """Return allocated CPUs and useful Vina threads.

    cpu=0/missing means auto-detect on the actual compute worker, not the
    Streamlit frontend. Vina cannot use more simultaneous independent
    search threads than its exhaustiveness specifies.
    """
    allocated = allocated_cpu_count()
    requested = int(config.get("cpu", 0) or 0)
    available = min(allocated, requested) if requested > 0 else allocated
    return available, max(1, min(available, int(config["exhaustiveness"])))


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


def _run_vina_cli(config, ligand, seed, cpu_threads, output, checkpoint):
    """Windows-compatible Vina executable; derive scores from its PDBQT output."""
    import re
    import subprocess
    exe = Path(os.environ["PANDOC_VINA_EXE"])
    if not exe.is_file():
        raise FileNotFoundError(f"Vina executable not found: {exe}")
    args = [
        str(exe), "--receptor", str(config["receptor"]),
        "--ligand", str(ligand["path"]),
        "--center_x", str(float(config["center"][0])),
        "--center_y", str(float(config["center"][1])),
        "--center_z", str(float(config["center"][2])),
        "--size_x", str(float(config["size"][0])),
        "--size_y", str(float(config["size"][1])),
        "--size_z", str(float(config["size"][2])),
        "--cpu", str(cpu_threads),
        "--seed", str(int(seed)),
        "--exhaustiveness", str(int(config["exhaustiveness"])),
        "--num_modes", str(int(config["poses"])),
        "--out", str(output),
    ]
    # Print the reproducible docking settings once per search.
    # Do not change the user-selected parameters or duplicate Vina scores.
    def _xyz(values):
        return ", ".join(f"{float(value):g}" for value in values)

    print(f"=== AutoDock Vina | {ligand['name']} | seed {int(seed)} ===", flush=True)
    print(f"Receptor: {Path(config['receptor']).name}", flush=True)
    print(f"Ligand: {Path(ligand['path']).name}", flush=True)
    print(f"Box center (Å): {_xyz(config['center'])}", flush=True)
    print(f"Box size (Å): {_xyz(config['size'])}", flush=True)
    print(
        f"Exhaustiveness: {int(config['exhaustiveness'])} | "
        f"CPU: {int(cpu_threads)} | Modes: {int(config['poses'])} | Seed: {int(seed)}",
        flush=True,
    )
    checkpoint("preparing_vina_maps", "Preparing Vina maps in external executable...")
    checkpoint("vina_search", "Running external Vina executable...")
    # Stream native Vina stdout/stderr as the search runs instead of waiting
    # for subprocess.run(capture_output=True) to return.
    import queue
    import threading
    import time

    started = time.monotonic()
    deadline = started + max(1, int(os.environ.get("PANDOC_VINA_TIMEOUT", "3600")))
    messages = queue.Queue()
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def collect_output():
        try:
            for line in proc.stdout:
                messages.put(line.rstrip("\r\n"))
        except Exception as exc:
            messages.put(f"Vina output reader error: {exc}")
        finally:
            messages.put(None)

    reader = threading.Thread(target=collect_output, daemon=True)
    reader.start()
    output_tail = []
    finished_reading = False
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Vina exceeded {int(deadline - started)}s runtime limit")
            try:
                line = messages.get(timeout=0.25)
                if line is None:
                    finished_reading = True
                else:
                    print(line, flush=True)
                    output_tail.append(line)
                    if len(output_tail) > 60:
                        output_tail.pop(0)
            except queue.Empty:
                pass

            if finished_reading and proc.poll() is not None:
                break
        code = proc.wait(timeout=5)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout:
            proc.stdout.close()

    elapsed = int(time.monotonic() - started)
    if code != 0:
        raise RuntimeError(
            f"Vina executable failed (exit {code}): " + "\n".join(output_tail)[-1600:]
        )
    poses_path = Path(output)
    if not poses_path.is_file():
        raise RuntimeError("Vina returned no docked PDBQT file.")
    checkpoint("reading_scores", "Reading scores from Vina PDBQT output...")
    values = re.findall(
        r"^REMARK VINA RESULT:\s*(-?\d+(?:\.\d+)?)",
        poses_path.read_text(errors="replace"), re.M,
    )
    if not values:
        raise RuntimeError("Vina PDBQT has no REMARK VINA RESULT scores.")
    return [[float(value)] for value in values]


def _run_search(directory, config, ligand, seed, cpu_threads, stage_callback=None):
    from rdkit import Chem
    from meeko import PDBQTMolecule, RDKitMolCreate

    directory = Path(directory)
    cpu_threads = max(1, int(cpu_threads))

    def checkpoint(stage, message, **progress):
        """Record the exact step before starting a potentially costly operation."""
        if not os.environ.get("PANDOC_VINA_EXE"):
            print(message, flush=True)
        if stage_callback is not None:
            stage_callback(stage, **progress)

    print(f"Starting docking: {ligand['name']} / seed {seed}", flush=True)

    stem = directory / f"{ligand['id']}_seed{seed}"
    output = str(stem.with_suffix(".pdbqt"))
    if os.environ.get("PANDOC_VINA_EXE"):
        scores = _run_vina_cli(config, ligand, seed, cpu_threads, output, checkpoint)
    else:
        from vina import Vina
        vina = Vina(sf_name="vina", cpu=cpu_threads, seed=int(seed), verbosity=2)
        vina.set_receptor(config["receptor"])
        vina.set_ligand_from_file(ligand["path"])
        checkpoint("preparing_vina_maps", "Computing Vina affinity maps for this search...")
        vina.compute_vina_maps(center=config["center"], box_size=config["size"])
        print(f"=== AutoDock Vina live output · {ligand['name']} · seed {seed} ===", flush=True)
        print(vina, flush=True)
        checkpoint("vina_search", "Performing Vina search...")
        vina.dock(exhaustiveness=config["exhaustiveness"], n_poses=config["poses"])
        checkpoint("writing_poses", "Vina search returned; exporting docked PDBQT poses...")
        vina.write_poses(output, n_poses=config["poses"], overwrite=True)
        checkpoint("reading_scores", "PDBQT poses saved; retrieving Vina scores...")
        scores = vina.energies(n_poses=config["poses"])
    # Vina's native Python binding does not reliably print the final affinity
    # table to captured stdout. Publish the actual returned energies immediately,
    # before optional RDKit/Meeko pose reconstruction and RMSD calculations.
    if not os.environ.get("PANDOC_VINA_EXE"):
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
                try:
                    row["reference_rmsd_A"] = reference_rmsd(reference, pose)
                except ValueError as exc:
                    # RMSD is undefined when the reconstructed docking pose and
                    # crystal reference are not the same chemical structure.
                    # Retain the Vina score and pose, but NEVER label this as
                    # successful redocking or assign a fabricated RMSD.
                    row["rmsd_validation_status"] = "not_comparable"
                    row["rmsd_warning"] = str(exc)
                    pose.SetProp("rmsd_validation_status", "not_comparable")
                    pose.SetProp("rmsd_warning", str(exc))
                    print(
                        f"RMSD NOT COMPARABLE: {ligand['name']} / seed {seed} "
                        f"/ pose {rank}: {exc} "
                        "Check ligand connectivity, protonation, stereochemistry "
                        "and reference identity before interpreting redocking.",
                        flush=True,
                    )
                else:
                    row["rmsd_validation_status"] = "valid"
                    pose.SetProp("reference_rmsd_A", str(row["reference_rmsd_A"]))
                    pose.SetProp("rmsd_validation_status", "valid")
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
        # Search completion is measurable; Vina's inner optimization is not.
        print(f"DOCKING_PROGRESS 0/{total} (0.0%) searches completed", flush=True)

        available_cpu, vina_threads = vina_cpu_threads(config)
        exhaustiveness = max(1, int(config["exhaustiveness"]))

        # Use all worker CPUs that Vina can employ for these independent
        # Monte-Carlo runs. The user-defined exhaustiveness is never silently
        # increased: doing so would change the scientific search protocol.

        if not os.environ.get("PANDOC_VINA_EXE"):
            print(
                f"Vina compute: {available_cpu} CPU(s); {vina_threads} threads; "
                f"exhaustiveness={exhaustiveness}; serialized shared-map reuse disabled.", flush=True,
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
            print(f"Completed docking search {completed_searches}/{total}", flush=True)
            print(
                f"DOCKING_PROGRESS {completed_searches}/{total} "
                f"({100.0 * completed_searches / total:.1f}%) searches completed",
                flush=True,
            )
            (directory / "results.json").write_text(json.dumps(results, indent=2))
            update(
                "running",
                total=total,
                current=f"{completed_searches}/{total} docking searches completed",
                cpu_threads=vina_threads,
            )

        if results and not os.environ.get("PANDOC_VINA_EXE"):
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
