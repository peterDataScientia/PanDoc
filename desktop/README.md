# PanDockLab Desktop (Windows)

This folder adds a Windows distribution **without changing** the Streamlit Cloud entry point `app.py`.

## Distribution
Run **Actions → Build PanDockLab Windows Installer → Run workflow** on GitHub.
If CI succeeds, download the `PanDockLab-Setup-Windows-x64` workflow artifact. It contains the installer. A successful build is **not** proof that docking workflows have passed end-to-end tests.

- Windows 10/11 x64, Microsoft Edge WebView2 runtime required.
- Installs PanDockLab shortcut in Start Menu; optional desktop shortcut.
- Streamlit runs on an ephemeral localhost port bound to `127.0.0.1`, inside a pywebview window.
- Closing the window stops the local Streamlit server.
- Local session data is kept under `%USERPROFILE%\PanDocData\pandoc` (not silently deleted by uninstaller).
- Internet is required for external searches and remote workers. Credentials and worker access are **not** bundled.
- Docking software may need extra validation on actual Windows hosts (including binary compatibility, scientific package resources, and subprocess executables).
- Installer is currently unsigned; Windows SmartScreen may warn until code signing is arranged.


## In-app updates (Windows desktop only)

The **Software updates** panel is in the PanDockLab sidebar. Select **Check for updates**, then **Download and install update** when a newer stable release exists. The download is verified against the matching SHA-256 release manifest before installation; if verification fails the installed application remains untouched. **Save running experiments and close the PanDockLab window** once the verified updater reports it is ready. A detached Windows helper waits until that installed PanDockLab executable exits, installs the update in place, and reopens PanDockLab. A failed update leaves a diagnostic in `%USERPROFILE%\\PanDocData\\updates\\update-result.log`.

- The first updater-enabled installer must be installed manually. Older beta installers cannot acquire the updater automatically.
- Only published non-prerelease `pandoc-desktop-vX.Y.Z` tags in the official `peterDataScientia/PanDoc` GitHub repository are considered.
- Each release includes **PanDockLab-Setup-Windows-x64.exe** and its `.sha256` manifest, plus legacy **PanDoc-Setup-Windows-x64.exe** and its `.sha256` manifest for already-installed updaters.
- Tag releases only after testing. GitHub Actions builds the executable and publishes both files on a `pandoc-desktop-vX.Y.Z` tag push; `desktop/VERSION` must match `X.Y.Z`.
- The updater does not run on the hosted Streamlit app, and does not modify data in `PanDocData`.
- A SHA-256 checksum checks download integrity; it is not a substitute for Windows Authenticode signing. The installer is currently unsigned and Windows may show a SmartScreen prompt.
- A GitHub Actions ZIP artifact is a test build, **not** an update release.

## Naming and safe upgrade migration

PanDockLab is the new public name, previously PanDoc. This initial rename **deliberately retains** the original Inno Setup AppId, install location, internal `PanDoc.exe`, `pandoc` Python package, `PANDOC_*` environment variables, and the `%USERPROFILE%\\PanDocData` data folder. This prevents new installations from overwriting neither project data nor breaking the existing updater. The visible Start Menu and desktop shortcut are named **PanDockLab**. Windows executable names, persistent directory names and the GitHub repository slug can be migrated later after an end-to-end upgrade test.

**Critical:** Existing installers check `peterDataScientia/PanDoc`, `pandoc-desktop-vX.Y.Z`, and `PanDoc-Setup-Windows-x64.exe`. Keep those available in releases until all supported older clients have upgraded. The workflow publishes an identical PanDockLab-named installer copy for new installations. Do not rename the GitHub repository before the legacy updater's redirect behavior is confirmed. The initial public branding does not constitute a verified rename of the GitHub repository, Streamlit Cloud URL, or Windows binary signature.

## Official PanDockLab artwork
The user-approved subtitle-free logo is saved as `assets/pandocklab_selected_logo.webp.b64`, a portable base64-encoded WebP file. The web interface displays that exact artwork. `desktop/build_icon.py` derives a compact emblem from **the same image** for the Windows executable, Start Menu shortcuts and Inno Setup installer icon; it does not draw a substitute icon. The original full-resolution source is retained separately outside this repository as a conversation image. Keep the source image unchanged unless the software owner approves another design.

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
