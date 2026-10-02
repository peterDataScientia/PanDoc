# PanDoc

A guided Streamlit workbench for receptor inspection, Meeko preparation, crystallographic-ligand redocking and AutoDock Vina docking experiments.

## Run locally

Use Python 3.11 on Linux (recommended), macOS or Windows with WSL2.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

PDBFixer and OpenMM are included in the main requirements. Heavy-atom reconstruction is available by checking the repair option during receptor preparation; it is not run automatically.

Docker:

```bash
docker build -t pandoc .
docker run --rm -p 8501:8501 pandoc
```

Streamlit Community Cloud: select this repository, `main`, `app.py`, and Python 3.11. Docking uses server CPU resources; start with small batches. Meeko and Vina versions are pinned. A server with a container and persistent storage is preferable for longer computations.


### Deployment error: Vina / Boost on Python 3.14

If logs show Python 3.14 and `Boost library location was not found`, the installer is attempting to compile Vina because that runtime has no compatible prebuilt Vina 1.2.7 wheel. Deploy with **Python 3.11** instead. Adding a `runtime.txt` does not change an existing Community Cloud app's Python interpreter.

Community Cloud requires deleting and redeploying an existing app to change Python. Preserve your app settings and any secrets first, then redeploy with repository `peterDataScientia/PanDoc`, branch `main`, entrypoint `app.py`, and custom subdomain `pandoc`. In **Advanced settings**, explicitly select **Python 3.11** before clicking Deploy. Restarting the Python 3.14 app will not change its interpreter.

The requirements file now requires a prebuilt Vina wheel so unsupported runtimes fail clearly rather than starting a C++ build. A Linux CPython 3.11 wheel is available and has been verified. This guard does not itself change the cloud runtime.

Official guidance: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/upgrade-python

## Workflow

1. Upload PDB/mmCIF. Select protein chains, retained components and reference ligand. Choose alternate conformations per residue.
2. Prepare the receptor with Meeko. Optionally upload a curated receptor or reconstruct missing heavy atoms using PDBFixer. Record pH context and explicit residue template assignments. No blanket deletion of failed residues and no automatic missing-loop reconstruction.
3. Provide the exact reference ligand isomeric SMILES. Bond orders are assigned to extracted crystal coordinates. The original reference remains separate. Inspect ligand chemistry before continuing.
4. Define a box, redock across seeds and inspect reference RMSD. RMSD uses RDKit CalcRMS, heavy atoms and symmetry handling in the fixed receptor coordinate frame. No independent ligand alignment is performed.
5. Prepare up to 25 candidate ligands from multi-record SDF or SMILES. Run Vina jobs in a separate process. Inspect poses, scores and export the complete experiment.

## Scientific behavior and limits

- pH is recorded as context; PanDoc does not predict site-specific pKa or automatically enumerate protonation states/tautomers. User-reviewed chemical states are required.
- Missing-heavy-atom counts are preliminary heuristics. Meeko template matching is the actual chemical check. OXT, modified residues and chain boundaries require individual interpretation.
- PDBFixer repairs are modeled coordinates. Review pocket repairs; missing loops require a separate curated model.
- A curated receptor must remain in the original reference coordinate frame. Align the receptor externally if necessary; do not independently align ligand poses for RMSD.
- Alternate coordinates are selected consistently by residue. Only the first model is used. Verify chain IDs after mmCIF-to-PDB conversion, especially for large assemblies.
- Complex metal, covalent and specialized water docking are outside the first-version scope. Successful file preparation does not establish scoring-function suitability.
- Redocking summarizes top-ranked and best-pose recovery separately. The default 2 Å threshold is editable and is a screening convention, not evidence of affinity prediction.
- Validation fingerprints include receptor preparation, box, search settings, seeds and reference identity. A completed matching job indicates matching settings, not automatically satisfactory recovery.
- No interaction analysis or PANVIZ integration is implemented yet. Pose inspection and scientific downloads are implemented.
- Current jobs are session-scoped subprocesses, not a distributed queue. Cancellation is cooperative between searches. Server restarts interrupt jobs. Refresh status manually.
- Set `PANDOC_DATA_DIR` to a writable persistent directory if needed. Experiments are isolated by random IDs, but session reconnection and authentication are not implemented. Download the experiment before ending the session. A public multi-user deployment needs resource limits and a durable queue.
- 3D visualization loads the 3Dmol JavaScript viewer; the browser needs access to its CDN.

## Verification

```bash
python -m compileall -q app.py pandoc
pip install pytest
python -m pytest -q
```

Tests cover alternate conformations, incomplete residues, box calculation, invalid configurations, fixed-frame RMSD, atom reordering, preserved reference coordinates, Meeko ligand export/reconstruction, a real small Vina docking run, and Streamlit screens with empty and populated experiments. The small peptide/ethanol test checks software integration; it is not a biological docking benchmark. GitHub Actions installs dependencies and runs the checks on pushes and pull requests.

## Layout

`app.py` contains the guided interface. `pandoc/core.py` handles inspection and chemistry, `pandoc/jobs.py` launches isolated jobs, and `pandoc/worker.py` runs Vina and reconstructs SDF poses. `tests/` contains scientific regression checks.

Meeko: https://github.com/forlilab/Meeko

AutoDock Vina: https://github.com/ccsb-scripps/AutoDock-Vina

PDBFixer: https://github.com/openmm/pdbfixer
