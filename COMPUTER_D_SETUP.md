# PanDoc Computer D — Vina-only Windows worker

PanDoc **stays on Streamlit Community Cloud**. Runtime Computer D submits an encrypted job to a temporary GitHub branch. The Windows worker polls GitHub over HTTPS and runs the already-installed AutoDock Vina 1.2.7. Tailscale is for private computer management; job transport is **not** routed through the Tailscale IP.

## Windows setup (DESKTOP-MG93973)

The worker expects `C:\Program Files (x86)\vina_1.2\vina.exe`, already verified as AutoDock Vina v1.2.7.

1. In CMD, run `py -3.13 -m pip install requests cryptography`.
2. Copy `computer_d_worker.py` and `start_computer_d.cmd` from this repository to `%USERPROFILE%\PanDoc-Vina-Worker`.
3. Open `start_computer_d.cmd` (without Administrator privileges).
4. Enter the dedicated fine-grained GitHub token and the **same** Fernet `PANDOC_JOB_KEY` that Streamlit already uses. Inputs are hidden, not saved. Never share these credentials.
5. In PanDoc Streamlit, select **Computer D · Tailscale Vina**, prepare a receptor and ligand as normal, and run a small docking test.

The GitHub token must have Contents read/write permission for `peterDataScientia/PanDoc`. Streamlit still requires its existing `GITHUB_TOKEN` and `PANDOC_JOB_KEY` secrets. To use a dedicated worker-only token, enter it in the worker prompt. Keep the worker CMD open while you want to receive jobs.

Only docking runs on Windows: receptor/ligand preparation, PDBQT-to-SDF conversion, and symmetry-aware RMSD remain on the Streamlit host. Computer A/B/C are unchanged.

Limits: 500 prepared ligands, 5 seeds, 20 poses, 60 Å box dimensions, max 4 CPU threads, 1 hour/search, 35 MB encrypted transport per direction. One worker process at a time. Cancellation is observed between searches, not mid-search. Failed jobs may leave temporary branches that need cleanup.

**Deployment limitation:** A network connection and a real docking test are required to confirm end-to-end operation. Do not claim that the worker is online until its CMD is running. The worker does not automatically become available just because the Streamlit runtime choice appears.
