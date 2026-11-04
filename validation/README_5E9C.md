# 5E9C deposited glycan redocking: reproducible diagnostic (2026-11-04)

**Source:** [successful three-seed GitHub Actions run](https://github.com/peterDataScientia/PanDoc/actions/runs/41781221282), job `126412968580`. This record transcribes the actual Vina scores and fixed-receptor-frame heavy-atom reference RMSDs printed by the job, rather than estimated results.

## Inputs and preparation

- HPSE1 X-ray structure: PDB **5E9C**, protein chains A+B.
- Glycan reference: chain C, four linked CCD components `5KV`, `IDR`, `SGN`, `UAP`; 66 deposited heavy atoms; three explicit inter-residue LINKs.
- Missing terminal `O1` names are accepted only when the associated anomeric `C1` is explicitly linked. No experimental heavy atom is invented.
- Chemical preparation: 5 sulfate and 2 carboxylate groups deprotonated (nominal formal charge **-7**) at assumed pH **5.5**. Crystal heavy-atom positions preserved; hydrogens are modeled.
- Protein: PDBFixer heavy-atom repair and PROPKA pH 5.5 review. Ambiguous protonation states are flagged, not independently experimentally established.
- AutoDock Vina 1.2.7, exhaustiveness **8**, **5** poses saved per search, seeds **2026**, **2027**, **2028**, **2 CPU threads**.
- Search center (Å): **(-19.046, 18.4325, 59.552)**.
- Search dimensions (Å): **(22.382, 27.803, 19.258)**.

## Pose-recovery audit

| Seed | Top-ranked score (kcal/mol) | Top-ranked RMSD (Å) | Lowest RMSD among 5 poses (Å) | Best-RMSD pose rank |
|---|---:|---:|---:|---:|
| 2026 | -9.370 | 1.183 | 1.183 | 1 |
| 2027 | -9.473 | 1.071 | 1.071 | 1 |
| 2028 | -9.187 | 2.733 | 0.955 | 2 |

At a 2 Å criterion, **2 of 3 seeds recovered the experimental reference in the top-ranked pose**; **3 of 3** recovered a pose within 2 Å among the five reported poses. The score ranking did **not** consistently select the lowest-RMSD pose. See [full 15-pose CSV](5e9c_redocking_2026-11-04.csv).

## Interpretation and limits

The experiment supports **deposited pose recovery under these specific preparation and docking choices**. It does **not** independently establish a physically reliable binding free energy, a universal docking protocol for all HPSE1 inhibitors, or the predictive value of protein–ligand interaction descriptors. DP4 is a highly charged substrate-related carbohydrate, whereas candidate HPSE1 inhibitors can be chemically diverse. Before production docking, review catalytic-site protonation (particularly Glu225/Glu343 using consistent residue numbering), ligand stereochemistry and linkage chemistry, and suitability of Vina scoring for sulfated glycans. Any subsequent ligand cohort should have explicit applicability and reproducibility checks.

The complete run output is preserved in the linked GitHub Actions job. The CSV is an audit transcription and contains no new calculations.
