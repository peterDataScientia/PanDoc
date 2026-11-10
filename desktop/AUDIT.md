# PanDoc Windows Desktop audit

Scope: Windows desktop branch only (`feature/windows-desktop-installer`). This is a release-gating checklist, not a claim that all scientific workflows have been validated.

## Confirmed in previous Windows CI

- Scientific dependency installation and PyInstaller onedir packaging.
- Official AutoDock Vina Windows executable download and `--help`.
- Frozen PanDoc import + Fernet encryption self-test.
- Frozen Streamlit health endpoint and Inno Setup artifact creation.
- These checks establish packaging/startup, not a working receptor-to-results docking workflow.

## Issues found during installed beta testing and code audit

| Component | Problem | Mitigation | Release verification |
| --- | --- | --- | --- |
| PROPKA | `sys.executable -m propka` runs PanDoc.exe in frozen mode, but launcher had no propka command dispatch | Added frozen `-m propka` entry point, `propka.__main__` packaging, CLI diagnostics | Verify a real protein produces `.pka` and parsed titratable residue records |
| Meeko | Receptor CLI `sys.executable -m meeko.cli.mk_prepare_receptor` runs PanDoc.exe; stdout/stderr and app-install directory can be unsuitable for writing | Dispatch Meeko through runpy; write diagnostic log beside output and restore streams | Run a complete receptor preparation and verify atom preservation |
| Docking | Local job spawns `sys.executable -u -m pandoc.worker JOB_DIR`, unsupported by original desktop launcher | Added `-u -m pandoc.worker` dispatch and writable log streaming | CI must show worker process writes expected status; dock known ligand and inspect poses/scores |
| Frozen Streamlit | Incorrect development-mode configuration prevented custom server port | Set `global.developmentMode=false` | Health endpoint already passed in earlier CI |
| Frozen imports | Installer started but app failed importing encryption dependency | Collect cryptography package and validate full imports with Fernet round trip | Self-test previously passed |
| Desktop UI | User reported dark selectors, tables, and checkboxes in otherwise light UI | Explicit desktop-only Streamlit theme flags | Visual Windows 10/11 QA of native widgets, including dataframe canvas |
| Live logs | Windowed bootloader lacks Python standard streams, impacting scientific subcommands and worker log | Redirect subcommand streams to logs; restore Python streams | Validate logs update during actual docking |

## Must pass before promoting to stable release

- Fresh Windows 10/11 installation without Python/Conda; Edge WebView2 present or clearly diagnosed.
- Launch and render the actual app, not just server health.
- PDB search/upload and download; receptor repair; PROPKA real-pH pKa estimation and review; Meeko receptor output.
- Ligand preparation (RDKit + Molscrub + Meeko), valid ligand PDBQT; no silent state changes.
- Local Vina docking at fixed seeds/grid; results PDBQT `REMARK VINA RESULT` scores; SDF conformer and affinity consistency; optional symmetry-aware RMSD.
- Cancel/restart and failure messaging; live worker logs; exported files.
- Data paths and file permissions under a nonadmin Windows user.
- Check all dataframes, selectboxes, radio/checkbox widgets, tooltips, scrolling, file uploader, and download controls with light theme.
- Test hosted/cloud app separately before merging branch (main is intentionally unchanged).

## Build policy

GitHub Windows workflow must fail on dependency errors, frozen imports, PROPKA dispatch, local worker dispatch, startup failure, and installer packaging errors. Preserve raw logs. Do not interpret a successful installer build as proof of docking or GUI correctness.
