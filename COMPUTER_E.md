# Computer E — Full PanDoc local runtime (independent of Computer D)

Computer E is a **complete local PanDoc deployment** on the same Windows machine
that currently hosts Computer D. It is **not** the existing Vina-only GitHub
worker. The Computer D worker, its GitHub encrypted-job transport, environment,
and Windows Scheduled Task remain unchanged.

## First installation (on the Windows machine)

Prerequisites:
- Conda (Miniforge or Miniconda), Git, and Tailscale. Python 3.11 is installed inside the isolated Conda environment.
- Sufficient free disk space for packages, downloads, jobs, and outputs.
- Tailscale connected to the intended private tailnet.

From an ordinary PowerShell terminal:

```powershell
$dir = Join-Path $env:TEMP "PanDoc-Computer-E-Bootstrap"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
curl.exe -fL -o (Join-Path $dir "computer_e_install.ps1") "https://raw.githubusercontent.com/peterDataScientia/PanDoc/main/computer_e_install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "computer_e_install.ps1")
```

Installer clones PanDoc to `%USERPROFILE%\PanDoc-Computer-E\source`,
creates an isolated Conda environment under `PanDoc-Computer-E\\conda-env` and separate data/log folders, installs dependencies,
then checks imports. Check the script before executing it.

## Start (ordinary PowerShell)

```powershell
& "$env:USERPROFILE\PanDoc-Computer-E\source\computer_e_run.ps1"
```

Open **http://127.0.0.1:8502** on the host to confirm PanDoc starts. Scientific
functions, local jobs, and session files execute on the local machine.

## Private remote connection with Tailscale Serve

In an **Administrator** PowerShell terminal on the host, run:

```powershell
tailscale serve --bg 8502
tailscale serve status
```

Tailscale will show the HTTPS URL available **only within your tailnet**. It may
prompt to enable tailnet HTTPS certificates. This command does not use Funnel.
Open the given HTTPS URL on a device signed into and authorized by your tailnet.

**Security:** Do not use `tailscale funnel` or expose Streamlit directly to
the public Internet. Tailscale's ACLs determine who can reach Computer E.
This first implementation supports private authorized users, not anonymous
public access from Streamlit Community Cloud.

## Optional link from the hosted PanDoc

Set `PANDOC_COMPUTER_E_URL` in the hosted app's environment to the exact
private HTTPS URL shown by `tailscale serve status`. The hosted UI will show
an **Open Computer E — Full Runtime** link. It opens a *separate* full PanDoc
instance; the cloud Streamlit process cannot directly run inside a different
machine by switching a dropdown.

On Computer E itself the app displays **Computer E · Full Local Runtime**.

## Windows Vina dependency

The published Vina 1.2.7 Python wheels do not support Windows. Computer E
therefore installs all other packages from a generated
\`requirements-windows.txt\` (leaving the repository's Linux/Cloud
\`requirements.txt\` untouched), and uses an existing native Windows
\`vina.exe\` through \`PANDOC_VINA_EXE\`. The setup checks common executable
locations, including \`C:\Program Files (x86)\vina_1.2\vina.exe\`, and writes
the selected path to \`vina_path.txt\`. The launch script reads that file.

If Vina is installed elsewhere, set \`$env:PANDOC_VINA_EXE\` to its full path
before rerunning the installer. Validate its version and run a reference
redocking test before treating the backend as production-ready.

## Compatibility and limitations

- The same-machine CPU/RAM are shared between D and E; running both may
  contend for resources. Limit simultaneous docking as needed.
- Computer E has its **own** `source`, `conda-env`, and `data` folders.
- Python packages required by PanDoc are installed in the isolated environment.
  External executables/toolchains must still be present where required.
- Some package installs may fail on Windows, especially optional chemistry
  tools. The installer stops with an error; it does not claim readiness then.
- User sessions and files belong to the **Computer E** instance and do not
  automatically migrate from an open hosted session.
- This phase deliberately avoids changing the existing Computer D worker.
- To stop local Streamlit, press Ctrl+C. To stop Tailscale exposure, use
  `tailscale serve off` after verifying it will not remove unrelated serves.
- Do not put GitHub credentials or private API tokens into repository files.

## Current status

Infrastructure scripts and optional launch link are provided. Computer E is
**not live until installed and started on the Windows host**. No tool here
can execute these PowerShell steps directly on the user's Windows machine.
