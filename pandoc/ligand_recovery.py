"""Auditable recovery for bulk ligand preparation only.

No silent chemistry change: original and prepared SMILES, fragment decisions,
embedding method and all attempts are recorded for every recovered ligand.
"""
from __future__ import annotations


def prepare(smiles: str, *, allow_counterion_removal: bool = True):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    original = Chem.MolFromSmiles(str(smiles or "").strip())
    if original is None:
        raise ValueError("Invalid input SMILES.")
    fragments = list(Chem.GetMolFrags(original, asMols=True, sanitizeFrags=True))
    removed = []
    chosen = original
    if len(fragments) > 1:
        if not allow_counterion_removal:
            raise ValueError("Disconnected input; automatic counterion removal disabled.")
        # Only remove explicit small unbound counterions. Reject mixtures and
        # ambiguous multicomponent drugs rather than silently dropping chemistry.
        candidates = [f for f in fragments if f.GetNumHeavyAtoms() >= 6
                      and any(a.GetAtomicNum() == 6 for a in f.GetAtoms())]
        if len(candidates) != 1:
            raise ValueError("Ambiguous multicomponent compound; manual chemistry review required.")
        chosen = candidates[0]
        other = [f for f in fragments if f is not chosen]
        allowed = {"[Cl-]", "[Br-]", "[I-]", "[F-]", "Cl", "Br", "I",
                   "[Na+]", "[K+]", "[Li+]", "[Ca+2]", "[Mg+2]",
                   "O=C(O)C(F)(F)F", "O=C([O-])C(F)(F)F", "O=S(=O)(O)O",
                   "O=S(=O)([O-])O"}
        removed = [Chem.MolToSmiles(f, isomericSmiles=True) for f in other]
        canonical_allowed = {Chem.MolToSmiles(Chem.MolFromSmiles(s)) for s in allowed}
        if any(fragment not in canonical_allowed for fragment in removed):
            raise ValueError("Nonstandard second component; requires manual review: " + ", ".join(removed))
    cleaned_smiles = Chem.MolToSmiles(chosen, isomericSmiles=True)
    molecule = Chem.AddHs(Chem.Mol(chosen))
    attempts = [
        ("ETKDGv3_seed2026", 2026, False, "ETKDGv3"),
        ("ETKDGv3_seed2027", 2027, False, "ETKDGv3"),
        ("ETKDGv3_seed2028_randomCoords", 2028, True, "ETKDGv3"),
        ("ETKDGv2_seed2029_randomCoords", 2029, True, "ETKDGv2"),
    ]
    errors = []
    for method, seed, random_coords, variant in attempts:
        candidate = Chem.Mol(molecule)
        params = AllChem.ETKDGv3() if variant == "ETKDGv3" else AllChem.ETKDGv2()
        params.randomSeed = seed
        params.useRandomCoords = random_coords
        params.maxIterations = 1500
        try:
            rc = AllChem.EmbedMolecule(candidate, params)
            if rc != 0:
                errors.append(f"{method}: embedding returned {rc}")
                continue
            if AllChem.MMFFHasAllMoleculeParams(candidate):
                try:
                    AllChem.MMFFOptimizeMolecule(candidate, maxIters=400)
                except Exception as exc:
                    errors.append(f"{method}: MMFF optimization warning: {exc}")
            Chem.SanitizeMol(candidate)
            if candidate.GetNumConformers() != 1 or not candidate.GetConformer().Is3D():
                errors.append(f"{method}: not a 3D conformer")
                continue
            audit = {
                "original_smiles": Chem.MolToSmiles(original, isomericSmiles=True),
                "prepared_smiles": cleaned_smiles,
                "original_fragment_count": len(fragments),
                "removed_counterions": removed,
                "chemistry_modified": bool(removed),
                "conformer_method": method,
                "attempts": len(errors) + 1,
                "warnings": errors,
            }
            return candidate, audit
        except Exception as exc:
            errors.append(f"{method}: {exc}")
    raise ValueError("3D generation failed after four strategies: " + " | ".join(errors)[:800])
