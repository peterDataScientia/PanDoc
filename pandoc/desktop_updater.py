"""User-approved Windows desktop updates from official PanDoc GitHub Releases.

No updater is active on Streamlit Cloud. Release assets require a matching
SHA-256 checksum file; a workflow artifact ZIP is never treated as a release.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import json

REPOSITORY = "peterDataScientia/PanDoc"
TAG_PREFIX = "pandoc-desktop-v"
ASSET_NAME = "PanDoc-Setup-Windows-x64.exe"
CHECKSUM_NAME = ASSET_NAME + ".sha256"
API_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases?per_page=30"
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MIN_DOWNLOAD_BYTES = 1024 * 1024


class UpdateError(RuntimeError):
    """Safe, actionable update failure."""


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise UpdateError("Invalid desktop version metadata.")
    return tuple(int(v) for v in match.groups())


def installed_version() -> str:
    return os.environ.get("PANDOC_DESKTOP_VERSION", "0.2.0")


def _get(url: str, *, timeout: int = 8) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "PanDoc-Windows-Updater/1", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(1024 * 1024 + 1)
    except Exception as exc:
        raise UpdateError("GitHub update check unavailable. Check your connection.") from exc


def _asset(release: dict, name: str) -> dict | None:
    for item in release.get("assets", []):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def _validate_asset(asset: dict, *, name: str) -> str:
    url = asset.get("browser_download_url", "")
    prefix = "https://github.com/" + REPOSITORY + "/releases/download/"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise UpdateError("Untrusted release download URL.")
    if not url.endswith("/" + name):
        raise UpdateError("Unexpected release asset filename.")
    return url


def find_update(current: str) -> dict | None:
    """Select highest stable, immutable-versioned desktop release with both assets."""
    current_version = version_tuple(current)
    try:
        releases = json.loads(_get(API_URL).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("Invalid release metadata returned by GitHub.") from exc
    if not isinstance(releases, list):
        raise UpdateError("Invalid GitHub release response.")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX):
            continue
        try:
            version = version_tuple(tag[len(TAG_PREFIX):])
        except UpdateError:
            continue
        if version <= current_version:
            continue
        installer = _asset(release, ASSET_NAME)
        checksum = _asset(release, CHECKSUM_NAME)
        if installer is None or checksum is None:
            continue
        _validate_asset(installer, name=ASSET_NAME)
        _validate_asset(checksum, name=CHECKSUM_NAME)
        candidates.append((version, {
            "version": ".".join(map(str, version)),
            "installer_url": installer["browser_download_url"],
            "checksum_url": checksum["browser_download_url"],
            "release_url": release.get("html_url", ""),
            "notes": str(release.get("body") or "")[:1500],
        }))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _trusted_sha256(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateError("Release checksum is unreadable.") from exc
    pattern = re.compile(r"^([a-fA-F0-9]{64})\s+\*?" + re.escape(ASSET_NAME) + r"\s*$", re.M)
    match = pattern.search(text)
    if match is None:
        raise UpdateError("Installer checksum is missing or malformed.")
    return match.group(1).lower()


def download_verified(update: dict, destination: Path) -> Path:
    """Stream a release installer to user-owned storage; verify before atomic rename."""
    version_tuple(update["version"])
    installer_url = update["installer_url"]
    checksum_url = update["checksum_url"]
    _validate_asset({"browser_download_url": installer_url}, name=ASSET_NAME)
    _validate_asset({"browser_download_url": checksum_url}, name=CHECKSUM_NAME)
    checksum = _trusted_sha256(_get(checksum_url, timeout=15))
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / ("PanDoc-Setup-v" + update["version"] + ".exe")
    temporary = destination / ("PanDoc-Setup-v" + update["version"] + ".partial")
    digest = hashlib.sha256()
    downloaded = 0
    try:
        req = urllib.request.Request(installer_url, headers={"User-Agent": "PanDoc-Windows-Updater/1"})
        with urllib.request.urlopen(req, timeout=40) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("Installer exceeds permitted download size.")
                digest.update(chunk)
                out.write(chunk)
        if downloaded < MIN_DOWNLOAD_BYTES:
            raise UpdateError("Installer download is incomplete.")
        if digest.hexdigest() != checksum:
            raise UpdateError("SHA-256 mismatch. Download rejected; nothing was installed.")
        temporary.replace(target)
        update["verified_sha256"] = checksum
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("Could not download update. The installed app is unchanged.") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target


def schedule_install(installer: Path, executable: Path, update_dir: Path, expected_sha256: str) -> None:
    """Start independent helper, then user closes PanDoc; never overwrite a live EXE.

    The helper waits for this install's PanDoc processes to finish before
    launching the previously checksum-verified Inno Setup executable.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("Desktop updates require the installed Windows application.")
    installer = installer.resolve(strict=True)
    executable = executable.resolve(strict=True)
    update_dir = update_dir.resolve(strict=True)
    if installer.parent != update_dir or not installer.name.startswith("PanDoc-Setup-v"):
        raise UpdateError("Update installer is outside the approved update directory.")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise UpdateError("Verified installer SHA-256 is missing.")
    # Recheck immediately before launching helper. Helper checks once more
    # after the installed PanDoc process closes (protects the wait period).
    with installer.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != expected_sha256:
            raise UpdateError("Installer changed after verification; update cancelled.")

    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    helper = r'''
$ErrorActionPreference = "Stop"
$installer = INSTALLER
$program = PROGRAM
$log = LOGPATH
$expectedSha = EXPECTEDSHA
try {
    $until = (Get-Date).AddMinutes(15)
    do {
        $active = @(Get-CimInstance Win32_Process -Filter "Name = 'PanDoc.exe'" |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq $program })
        if ($active.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $until)
    if ($active.Count -gt 0) { throw "PanDoc was not closed; update cancelled." }
    $actualSha = (Get-FileHash -Path $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha -ne $expectedSha) { throw "Installer checksum changed before installation." }
    $result = Start-Process -FilePath $installer -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS'
    ) -PassThru -Wait
    if ($result.ExitCode -ne 0) { throw "Installer exit code: $($result.ExitCode)" }
    "Update installer completed successfully." | Out-File -FilePath $log -Encoding utf8
    if (Test-Path $program) { Start-Process -FilePath $program }
} catch {
    $_.ToString() | Out-File -FilePath $log -Encoding utf8
}
'''
    helper = (helper.replace("INSTALLER", ps_quote(str(installer)))
                    .replace("PROGRAM", ps_quote(str(executable)))
                    .replace("LOGPATH", ps_quote(str(update_dir / "update-result.log")))
                    .replace("EXPECTEDSHA", ps_quote(expected_sha256)))
    encoded = base64.b64encode(helper.encode("utf-16le")).decode("ascii")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            cwd=str(update_dir), creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise UpdateError("Windows could not start the updater helper.") from exc


def render_update_controls(st) -> None:
    """Show native Streamlit controls only in frozen, local desktop sessions."""
    if os.environ.get("PANDOC_DESKTOP_APP") != "1":
        return
    with st.sidebar.expander("Software updates", expanded=False):
        current = installed_version()
        st.caption(f"Installed PanDoc Desktop v{current}")
        if st.button("Check for updates", key="pandoc_desktop_check_updates"):
            try:
                st.session_state["pandoc_desktop_update"] = find_update(current)
                st.session_state["pandoc_desktop_update_checked"] = True
                st.session_state.pop("pandoc_desktop_update_error", None)
            except UpdateError as exc:
                st.session_state["pandoc_desktop_update_error"] = str(exc)
        if st.session_state.get("pandoc_desktop_update_error"):
            st.warning(st.session_state["pandoc_desktop_update_error"])
        available = st.session_state.get("pandoc_desktop_update")
        if available:
            st.success(f"PanDoc Desktop v{available['version']} is available.")
            url = available.get("release_url", "")
            if isinstance(url, str) and url.startswith(
                "https://github.com/" + REPOSITORY + "/releases/tag/"
            ):
                st.link_button("View release notes", url)
            st.caption("Save your work first. Close PanDoc after approving the installer.")
            if st.button("Download and install update", key="pandoc_desktop_install_update"):
                try:
                    update_dir = Path.home() / "PanDocData" / "updates"
                    with st.spinner("Downloading and verifying installer (SHA-256)..."):
                        installer = download_verified(available, update_dir)
                    schedule_install(installer, Path(sys.executable), update_dir,
                                     available["verified_sha256"])
                    st.success("Verified installer ready. Close the PanDoc window to finish updating; PanDoc will reopen.")
                except (UpdateError, OSError) as exc:
                    st.error(str(exc))
        elif st.session_state.get("pandoc_desktop_update_checked"):
            st.success("You are using the latest published stable desktop release.")
