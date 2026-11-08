# PanDoc Desktop (Windows)

This folder adds a Windows distribution **without changing** the Streamlit Cloud entry point `app.py`.

## Distribution
Run **Actions → Build PanDoc Windows Installer → Run workflow** on GitHub.
If CI succeeds, download the `PanDoc-Setup-Windows-x64` workflow artifact. It contains the installer. A successful build is **not** proof that docking workflows have passed end-to-end tests.

- Windows 10/11 x64, Microsoft Edge WebView2 runtime required.
- Installs PanDoc shortcut in Start Menu; optional desktop shortcut.
- Streamlit runs on an ephemeral localhost port bound to `127.0.0.1`, inside a pywebview window.
- Closing the window stops the local Streamlit server.
- Local session data is kept under `%USERPROFILE%\PanDocData\pandoc` (not silently deleted by uninstaller).
- Internet is required for external searches and remote workers. Credentials and worker access are **not** bundled.
- Docking software may need extra validation on actual Windows hosts (including binary compatibility, scientific package resources, and subprocess executables).
- Installer is currently unsigned; Windows SmartScreen may warn until code signing is arranged.

## Developer quick test

In a Windows Python 3.11 environment with repository requirements installed:

```powershell
python -m pip install -r desktop/requirements.txt
python desktop/launcher.py
```

## Release checklist
1. CI imports and PyInstaller build pass.
2. Install on a fresh Windows 10 and Windows 11 machine.
3. Test app startup, PDB lookup, PubChem, ligand/receptor preparation, docking, results/export.
4. Confirm uninstall and persistence of user data.
5. Publish a versioned release only after acceptance testing.

Note: packaged WebView2 is not part of the installer. Install the Microsoft Edge WebView2 runtime if missing.
