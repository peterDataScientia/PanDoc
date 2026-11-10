from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROTEIN_TEMPLATE_STATES = {
    "ASP": ("ASP", "ASH"),
    "GLU": ("GLU", "GLH"),
    "HIS": ("HIE", "HIP"),
    "LYS": ("LYN", "LYS"),
}

def parse_propka_summary(text: str, ph: float):
    """Parse PROPKA's summary and propose docking residue states at the selected pH."""
    marker = "SUMMARY OF THIS PREDICTION"
    if marker not in text:
        raise ValueError("PROPKA output does not contain a prediction summary.")
    block = text.split(marker, 1)[1]
    rows = []
    pattern = re.compile(r"^\s*(ASP|GLU|HIS|LYS|CYS|TYR|ARG)\s+(\d+[A-Za-z]?)\s+(\S)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", re.M)
    for match in pattern.finditer(block):
        residue, number, chain, pka, model_pka = match.groups()
        pka = float(pka)
        protonated = float(ph) < pka
        assignment = None
        if residue in PROTEIN_TEMPLATE_STATES:
            deprotonated, protonated_state = PROTEIN_TEMPLATE_STATES[residue]
            assignment = protonated_state if protonated else deprotonated
        if residue == "HIS":
            state = "HIP" if protonated else "neutral HIS (HID/HIE review)"
            assignment = "HIP" if protonated else None
            review = True
        elif residue == "CYS":
            state = "protonated CYS" if protonated else "thiolate; manual review"
            review = not protonated
        elif residue == "TYR":
            state = "protonated TYR" if protonated else "phenolate; manual review"
            review = not protonated
        elif residue == "ARG":
            state = "protonated ARG" if protonated else "neutral ARG; manual review"
            review = not protonated
        else:
            state = assignment
            review = abs(float(ph) - pka) <= 1.0
        rows.append({
            "residue": f"{chain}:{number}",
            "residue_name": residue,
            "chain": chain,
            "number": number,
            "pKa": pka,
            "model_pKa": float(model_pka),
            "pH": float(ph),
            "suggested_state": state,
            "template": assignment,
            "near_pKa": abs(float(ph) - pka) <= 1.0,
            "review_required": bool(review),
        })
    if not rows:
        raise ValueError("No titratable protein residues could be parsed from PROPKA output.")
    return rows

def predict_protein_states(pdb: str, directory, ph: float):
    """Run PROPKA on a repaired receptor and return proposed states plus raw provenance."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    inp = directory / "propka_input.pdb"
    inp.write_text(pdb)
    completed = subprocess.run(
        [sys.executable, "-m", "propka", inp.name],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = completed.stdout + "\n" + completed.stderr
    # The frozen GUI worker redirects the scientific CLI streams to a file.
    # Include that output in the existing protonation diagnostics.
    desktop_cli_log = directory / "propka_cli.log"
    if desktop_cli_log.is_file():
        log += "\n--- PROPKA DESKTOP CLI ---\n" + desktop_cli_log.read_text(errors="replace")
    (directory / "propka.log").write_text(log)
    output = directory / "propka_input.pka"
    if completed.returncode != 0 or not output.exists():
        raise ValueError("PROPKA pKa prediction failed. Inspect propka.log before preparing the receptor.")
    raw = output.read_text(errors="replace")
    rows = parse_propka_summary(raw, ph)
    return rows, raw

def template_assignments(rows, overrides=None):
    """Convert reviewed PROPKA proposals into Meeko --set_template assignments."""
    overrides = overrides or {}
    assignments = []
    for row in rows:
        state = overrides.get(row["residue"], row.get("template"))
        if state:
            assignments.append(f'{row["residue"]}={state}')
    return ",".join(assignments)

def enumerate_ligand_states(smiles: str, ph: float, max_states: int = 16):
    """Enumerate pH/tautomer ligand microstates with Molscrub and return unique RDKit molecules."""
    from rdkit import Chem
    try:
        from molscrub import Scrub
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown dependency"
        if missing == "molscrub":
            raise RuntimeError("Molscrub is not installed; ligand pH enumeration is unavailable.") from exc
        raise RuntimeError(
            f"Molscrub could not start because its dependency '{missing}' is missing."
        ) from exc
    except ImportError as exc:
        raise RuntimeError(f"Molscrub import failed: {exc}") from exc
    base = Chem.MolFromSmiles(smiles.strip())
    if base is None:
        raise ValueError("RDKit could not read the ligand SMILES.")
    # Molscrub defaults to a single ETKDG attempt; retry geometry generation
    # without altering protonation/tautomer selection or silently changing SMILES.
    strategies = (
        {"num_etkdg_attempts": 20, "use_random_coords": False},
        {"num_etkdg_attempts": 50, "use_random_coords": True},
    )
    failures = []
    unique = {}
    for strategy in strategies:
        try:
            scrub = Scrub(
                ph_low=float(ph), ph_high=float(ph),
                etkdg_rng_seed=2026, **strategy
            )
            candidate_states = {}
            for mol in scrub(base):
                key = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
                if key not in candidate_states:
                    candidate_states[key] = mol
                if len(candidate_states) >= int(max_states):
                    break
            if candidate_states:
                unique = candidate_states
                break
            failures.append("Molscrub returned no ligand microstates")
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append(str(exc)[:300])
    if not unique:
        raise ValueError(
            "Molscrub could not generate a 3D ligand state after normal and "
            "random-coordinate ETKDG retries. Review this compound's SMILES, "
            "ring system and protonation state. " + " | ".join(failures)[-600:]
        )
    result = []
    for i, (state_smiles, mol) in enumerate(unique.items(), 1):
        result.append({
            "index": i,
            "smiles": state_smiles,
            "formal_charge": int(Chem.GetFormalCharge(mol)),
            "mol": mol,
        })
    return result
