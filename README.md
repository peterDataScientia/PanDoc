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
2. Prepare the receptor with PDBFixer, PROPKA and Meeko. Optionally upload a curated receptor or reconstruct missing heavy atoms using PDBFixer, then run PROPKA at the selected preparation pH to obtain structure-dependent residue pKa predictions. PanDoc presents proposed residue states for review, flags residues near their predicted pKa, and requires explicit HID/HIE selection for neutral histidines before passing reviewed template assignments to Meeko. No blanket deletion of failed residues and no automatic missing-loop reconstruction.
3. Click **Find ligand chemistry from PDB** in the Reference ligand tab to retrieve the selected component's CCD SMILES, name, formula and formal charge. Review the molecular preview and side-by-side atom comparison. Manual SMILES entry remains editable. At the receptor preparation pH, PanDoc can use Molscrub to enumerate ligand protonation/tautomer microstates; review and select the intended state before preparation. Mismatches show clear actions and block preparation; deposited atom-name comparisons can identify absent atoms when naming is consistent. Custom residue names may require manual chemistry. Bond orders are assigned to extracted crystal coordinates; CCD ideal coordinates never replace the crystal reference. Click **Continue to Validate docking**.
4. Define a box, redock across seeds and inspect reference RMSD. RMSD uses RDKit CalcRMS, heavy atoms and symmetry handling in the fixed receptor coordinate frame. No independent ligand alignment is performed.
5. Prepare candidate ligands from multi-record SDF or SMILES. By default, PanDoc can enumerate pH-dependent protonation/tautomer microstates with Molscrub at the receptor preparation pH; review the generated states before docking. A maximum of 25 prepared ligand states is accepted per run. Run Vina jobs in a separate process, inspect poses and scores, and export the complete experiment.

## Scientific behavior and limits

- pH is an active preparation parameter. PanDoc runs PROPKA on the receptor to obtain structure-dependent residue pKa predictions and proposes residue states for review. Predictions do not silently override expert judgment: residues near their pKa are flagged, neutral histidines require explicit HID/HIE review, and manual/curated assignments remain available. For ligands, PanDoc can use Molscrub to enumerate protonation and tautomer microstates at the selected pH; users review/select the states used for docking.
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
- PDB chemistry lookup uses the RCSB Data API and optionally the CCD CIF download for atom-name checks. It is cached for one day per component. Service failures are shown with retry/manual-entry guidance. CCD chemistry is not a pH prediction, and matching atom counts alone do not establish matching connectivity. Lookup provenance is saved with the experiment.


## HEM and non-standard residue preparation

A retained \`HEM\` cofactor is **not** sent to Meeko's automatic CCD-template
builder: its iron coordination cannot be reliably reconstructed from an
ordinary ligand template. In **Prepare structures → Receptor**, explicitly
review the Fe-coordinating cysteine SG (including its Fe--SG distance) to use
the existing curated P450-like preparation. PanDoc prepares the protein using
the reviewed protonation states, restores the **original crystallographic HEM
coordinates**, converts using **AutoDockTools prepare_receptor4.py** and checks
the proximal thiolate and Fe retention. This requires an AutoDockTools-equipped
backend, such as the configured Computer C environment. Arbitrary His/other
heme coordination environments are **not** represented as P450 thiolates;
those receptors require a separately validated curated route.

The API accepts an optional \`heme_coordination_residue\` form field (for
example \`A:437\`) for the same reviewed route. Without an explicit donor,
the preparation **fails safely**, preserves HEM and explains what review is
required. PanDoc never automatically deletes cofactors to make Meeko pass.

For other **non-metal, noncovalent CCD components**, an explicit Meeko
CCD-template failure triggers at most **one** controlled retry, using an
RCSB \`<CCD>_ideal.sdf\` chemistry definition only when RDKit parses it as a
single chemical graph and its heavy-element inventory exactly matches the
deposited residue. The SDF does not replace deposited coordinates. The
original error, retry transcript and \`cofactor_resolution.json\` are saved
for review; the fallback does **not** establish charge accuracy, metal
coordination, or docking validity. RCSB discontinued \`_model.sdf\` in 2024,
so PanDoc uses the still-supported \`_ideal.sdf\`.

To supply reviewed local residue templates, configure
\`PANDOC_COF_TEMPLATE_DIR\` pointing to a directory containing files such as
\`ABC.json\` (curated Meeko JSON) or \`ABC.sdf\` (validated CCD component SDF).
The resolver never silently substitutes metal-containing or linked cofactors
with generic organic chemistry. Failed downloads and chemistry mismatches
remain visible, and downstream redocking validation is still required.


## Free GitHub Actions compute

PanDoc can offload docking calculations from Streamlit Community Cloud to GitHub-hosted Actions runners without a paid API server.

The Streamlit app automatically uses this backend when both of these Streamlit secrets are present:

```toml
GITHUB_TOKEN = "fine-grained GitHub token"
PANDOC_JOB_KEY = "Fernet key"
```

The repository must also contain the same `PANDOC_JOB_KEY` as a GitHub Actions repository secret. The token should be limited to this repository and needs Contents read/write plus Actions read/write permissions so PanDoc can create the encrypted temporary job branch, dispatch the workflow, inspect its status, download the result artifact, cancel jobs, and delete the temporary branch.

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Job inputs are compressed and encrypted before being committed to the temporary public branch. The workflow decrypts them only inside the GitHub runner. Docking logs and outputs are returned as a GitHub Actions artifact and are materialized back into the Streamlit session for the existing results UI. The temporary branch is deleted after successful result retrieval.

Workflow: `.github/workflows/pandoc-compute.yml`

## REST API

PanDoc includes a FastAPI service for programmatic access to the same scientific engine and the same shared compute service used by the Streamlit workbench. Docking compute is GitHub Actions only: `GITHUB_TOKEN` and `PANDOC_JOB_KEY` are required, and there is no local compute fallback.

Run locally:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Interactive API documentation is available at `/docs` and `/redoc`.

Core endpoints:

- `GET /api/v1/health` — service health.
- `GET /api/v1/versions` — software provenance.
- `GET /api/v1/compute` — report the active compute backend.
- `POST /api/v1/structures/inspect` — inspect an uploaded PDB/mmCIF structure.
- `POST /api/v1/proteins/protonation` — run PDBFixer (optional) and PROPKA at a selected pH and return reviewable residue-state proposals.
- `POST /api/v1/ligands/microstates` — enumerate ligand protonation/tautomer microstates with Molscrub.
- `POST /api/v1/receptors/prepare` — prepare a receptor with reviewed Meeko template assignments.
- `POST /api/v1/ligands/prepare` — prepare a selected ligand state for docking.
- `POST /api/v1/jobs/dock` — submit a Vina job to GitHub Actions when configured.
- `GET /api/v1/jobs/{job_id}` — read GitHub/local job status and materialize a completed Actions artifact.
- `GET /api/v1/jobs/{job_id}/results` — read docking results returned by the compute backend.
- `POST /api/v1/jobs/{job_id}/cancel` — cancel the GitHub Actions run or local worker.
- `GET /api/v1/jobs/{job_id}/bundle` — download completed job outputs as ZIP.

For public deployments, set `PANDOC_API_KEY`; protected endpoints then require an `X-API-Key` header. Set `GITHUB_TOKEN` and the same `PANDOC_JOB_KEY` used by the GitHub repository Actions secret to activate the GitHub Actions compute backend. `PANDOC_GITHUB_REPOSITORY` optionally overrides the default repository. `PANDOC_API_DATA_DIR` controls API-side materialized result storage and `PANDOC_API_MAX_UPLOAD_MB` controls the per-file upload limit. Optional browser clients can be allowed with `PANDOC_CORS_ORIGINS`.

A separate API container is provided:

```bash
docker build -f Dockerfile.api -t pandoc-api .
docker run --rm -p 8000:8000 \
  -e PANDOC_API_KEY=change-me \
  -e GITHUB_TOKEN=your-fine-grained-token \
  -e PANDOC_JOB_KEY=the-same-fernet-key-as-github-actions \
  -v pandoc-api-data:/data/pandoc_api \
  pandoc-api
```

`pandoc/compute.py` is the shared GitHub Actions compute gateway used by both Streamlit and FastAPI. Streamlit Community Cloud therefore remains a lightweight UI while docking compute runs remotely on GitHub Actions. A separately reachable FastAPI URL is only required when an external program needs HTTP endpoints.

## Verification

```bash
python -m compileall -q app.py pandoc
pip install pytest
python -m pytest -q
```

Tests cover alternate conformations, incomplete residues, box calculation, invalid configurations, fixed-frame RMSD, atom reordering, preserved reference coordinates, Meeko ligand export/reconstruction, a real small Vina docking run, and Streamlit screens with empty and populated experiments. The small peptide/ethanol test checks software integration; it is not a biological docking benchmark. GitHub Actions installs dependencies and runs the checks on pushes and pull requests.

## Layout

`app.py` contains the guided interface. `pandoc/compute.py` provides the GitHub Actions compute backend, `pandoc/core.py` handles inspection and chemistry, and `pandoc/worker.py` runs Vina and reconstructs SDF poses inside the Actions runner. `tests/` contains scientific regression checks.

Meeko: https://github.com/forlilab/Meeko

AutoDock Vina: https://github.com/ccsb-scripps/AutoDock-Vina

PDBFixer: https://github.com/openmm/pdbfixer

### Publication redocking overlay
After redocking, use **Validate docking → Figure pose**, or **Explore results → Inspect pose**. The ligand-only interactive view shows crystallographic heavy atoms in cyan and the selected redocked pose in magenta. Coordinates remain in the receptor frame; rotating the camera moves both ligands together and never fits a pose onto the reference. The first reported pose is selected by default. Seed, pose rank, and independently recomputed symmetry-aware heavy-atom RMSD appear in the app outside the figure. The exported panel contains only the ligand overlay and a compact centered color legend. Shaded ball-and-stick is the default; users can choose sticks, adjust both ligand colors, and switch between white and dark backgrounds without changing coordinates or the camera.

Browser exports render at high resolution: PNG is 3996 × 2340 pixels with 600-DPI physical-resolution metadata; PDF is 6.66 × 3.90 inches and embeds the raster panel. Camera orientation is preserved. The publication viewer uses an orthographic projection of the original 3D coordinates, with depth-sorted shaded atoms and bonds. Preview and export use the same drawing scene and camera. Export renders on a separate 2D canvas without resizing the visible viewer, WebGL buffers, or loading any external script. This illustration uses painter-style depth sorting rather than a ray tracer; inspect crossing bonds and adjust the viewing angle where needed. PDF export runs locally in the browser with no external PDF library, embedding lossless RGB pixels. Export errors appear beside the controls. No protein appears in this publication figure, and no docking calculation is repeated. Review the figure against journal requirements before submission.

### Search PDB within PanDoc
Use **Load complex → Search PDB** to search experimental structures by keywords, deposited protein name, exact PDB ID, UniProt accession, or ligand name/three-character component ID. The initial method is X-ray diffraction; advanced filters support organism, method, maximum resolution, and nonpolymer presence. The latter includes ions, additives and cofactors and does not guarantee a redocking reference. Exact PDB IDs bypass search filters. Results are paginated in groups of ten and cached for one hour. Search relevance is not a suitability score.

Select a result and click **View structure details** to review chain-specific molecule identities, source organisms, reported mutations, nonpolymer components, unmodeled residue counts and the publication. Links open RCSB structure and ligand-quality pages; PanDoc does not manufacture a quality score or treat missing validation as a pass. Click **Load this structure** to retrieve the deposited coordinate mmCIF (25-MB maximum), then inspect the 3D complex and explicitly select receptor chains and the reference ligand. Biological assemblies are not automatically substituted.

The experiment records the search criteria/query, chosen PDB ID, metadata, retrieval time, download URL, selection rationale and original-file SHA-256; the original mmCIF is included in the experiment archive. Changing the loaded complex clears previous preparation and active-result selections. Older calculation files remain in the experiment for provenance. API failures leave the currently loaded structure intact; file upload remains available.

For browser export regression checks, `?publication_demo=1` opens a clearly labelled synthetic ligand overlay using the production export component. It does not run docking or replace experiment data.

### Scientific assistant

Open **Scientific assistant** below the workflow. Add `GROQ_API_KEY = "your-key"` in Streamlit **App settings → Secrets**, or in the ignored `.streamlit/secrets.toml` locally. Environment variables are also supported. The default model is `openai/gpt-oss-120b`; optionally set `GROQ_MODEL` in secrets. Ask “What does docking exhaustiveness mean?” to test the connection.

Answers remain visible across reruns within the same session. Requests occur only on Ask. Optional context includes box settings and a chosen calculation's settings/status and first 50 result rows; the exact snapshot remains available with the answer. Molecular files, SMILES, ligand names, paths, and interactions are not automatically sent. This assistant explains information; it cannot execute calculations, change settings, or search literature. Model/account quotas apply.


### Automatic structure inspection

Loading a complex now runs coordinate screening before component cleanup. The
Structure details section shows the detected residue, explanation and next action;
Focus on selected residue highlights and focuses it in the molecular viewer.
Use selection and continue opens preparation directly. Prepare receptor rebuilds
missing heavy atoms by default, without an extra confirmation checkbox. Advanced
settings and diagnostic reports are collapsed until needed.
Standard amino-acid checks identify missing heavy atoms by name, unexpected atom
names, duplicate records, severe overlaps, terminal oxygen geometry, possible
chain breaks, alternate positions and zero occupancy. Waters, metals and other
components are flagged for review. LINK and SSBOND records are inventoried.

Alternate conformations are selected per residue. Suggested choices prefer the
more complete alternative, then higher mean occupancy; shared atoms are retained.
These are starting suggestions, not experimentally established best conformers.
Study binding-site alternatives in separate experiments when relevant.

Selection retains CONECT bonds between retained atoms and LINK/SSBOND records
whose two residues remain selected. Selection and preparation reports record
choices, detected issues, software versions and heavy-atom changes. Preparation
rejects unexpected heavy-atom loss or renaming rather than silently accepting it.
The assistant receives the selected issue and current preparation choices when
experiment context is enabled; molecular coordinates are not sent.

**Scope:** this is conservative coordinate and component screening, not a
MolProbity validation or electron-density assessment. Protonation is handled in
the preparation stage: PROPKA provides structure-dependent residue pKa
predictions, PanDoc converts them into reviewable residue-state proposals, and
reviewed assignments are passed to Meeko. PROPKA output is not treated as an
unquestionable ground truth, and neutral histidines require explicit HID/HIE
selection. Ligand protonation/tautomer states can be enumerated with Molscrub at
the selected pH and must be reviewed before docking. Missing loops,
metalloproteins, covalent components and unsupported templates can require
curated preparation. Structural success does not establish docking accuracy.

Design references: [wwPDB validation](https://www.wwpdb.org/validation/2016/XrayValidationReportHelp),
[PDB2PQR algorithms](https://pdb2pqr.readthedocs.io/en/latest/using/algorithms.html),
[PROPKA](https://propka.readthedocs.io/en/stable/command.html),
[Vina zinc workflow](https://autodock-vina.readthedocs.io/en/latest/docking_zinc.html).


## External browser automation contract

PanDoc supports external browser automation with Playwright, Selenium, or similar tools without bundling a browser driver into the PanDoc runtime.

The automation contract follows browser-testing best practice:

1. **Control widgets through accessible locators first.** External clients should prefer Playwright role/label selectors such as `getByRole()`, `getByLabel()`, and exact accessible names. Examples include **Workflow**, **Compute**, **PDB or mmCIF complex**, **Prepare receptor**, **Prepare reference ligand**, **Run redocking**, **Run docking**, and **Download complete experiment**.
2. **Do not target Streamlit's generated DOM classes or internal `data-testid` values.** They are framework implementation details and may change across Streamlit releases.
3. **Streamlit widget `key=` values are application/session identity, not browser selectors.** They make reruns deterministic inside PanDoc but are not promised to appear in the browser DOM.
4. **Use PanDoc-owned state markers only for synchronization.** PanDoc emits its own `pandoc-*` markers as an explicit application contract. These markers indicate state; they are not substitutes for accessible control locators.
5. **Wait on state, never arbitrary sleeps.** After an action that triggers a Streamlit rerun or remote calculation, wait for the expected PanDoc state marker and/or the expected accessible control to become visible/enabled.
6. **Use the browser download event for artifacts.** For Playwright, start `page.waitForEvent('download')` (or the equivalent `expect_download` API) before activating a PanDoc download button, then validate the suggested filename and saved artifact.

PanDoc-owned synchronization markers:

- `[data-testid="pandoc-app-ready"][data-state="ready"]`
- `[data-testid="pandoc-workflow-stage"]` with the current stage in `data-state`
- `[data-testid="pandoc-compute-node"]` with the selected compute node in `data-state`
- `[data-testid="pandoc-complex"]`, `pandoc-receptor`, and `pandoc-reference` with `pending` or `ready`
- `[data-testid="pandoc-job-status"]` with `queued`, `starting`, `running`, `completed`, `failed`, or `cancelled`; `data-value` contains the PanDoc job identifier
- `[data-testid="pandoc-ligand-state-job"]` and `[data-testid="pandoc-candidate-preparation-job"]` for preparation tasks
- `[data-testid="pandoc-reference-prepared"]` after reference preparation
- `[data-testid="pandoc-experiment-bundle"][data-state="ready"]` when the complete experiment ZIP is ready

A robust Playwright flow should therefore look conceptually like:

```javascript
await page.goto(PANDOC_URL);

await page.locator('[data-testid="pandoc-app-ready"][data-state="ready"]').waitFor();

await page.getByLabel('Workflow').getByText('1 · Load complex', { exact: true }).click();
// Operate the native upload control through its accessible label.
await page.getByLabel('PDB or mmCIF complex').setInputFiles('complex.pdb');

await page.locator('[data-testid="pandoc-complex"][data-state="ready"]').waitFor();

// Continue with role/label locators...
await page.getByRole('button', { name: 'Run redocking', exact: true }).click();

await page.locator('[data-testid="pandoc-job-status"][data-state="completed"]').waitFor({
  timeout: 30 * 60 * 1000,
});

await page.locator('[data-testid="pandoc-experiment-bundle"][data-state="ready"]').waitFor();
const downloadPromise = page.waitForEvent('download');
await page.getByRole('button', { name: 'Download complete experiment', exact: true }).click();
const download = await downloadPromise;
```

Because Streamlit reruns the application after many widget actions, external clients should reacquire locators after state-changing actions rather than caching element handles. Playwright locators are suitable because they are evaluated lazily and include auto-waiting/actionability checks.

For **PanDoc's own repository tests**, Streamlit's native `st.testing.v1.AppTest` remains the preferred fast test layer for widget/state logic. Real Playwright is appropriate as a separate end-to-end/browser compatibility layer when the deployed DOM, downloads, iframes, or real browser behavior must be verified. Playwright is not a PanDoc runtime dependency.

