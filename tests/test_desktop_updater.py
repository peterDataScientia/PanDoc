"""Offline regression checks for PanDoc's desktop release updater."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pandoc import desktop_updater as updates


def release(version, *, draft=False, prerelease=False, assets=True):
    tag = updates.TAG_PREFIX + version
    base = f"https://github.com/{updates.REPOSITORY}/releases/download/{tag}/"
    names = [updates.ASSET_NAME, updates.CHECKSUM_NAME] if assets else []
    return {
        "tag_name": tag, "draft": draft, "prerelease": prerelease,
        "html_url": f"https://github.com/{updates.REPOSITORY}/releases/tag/{tag}",
        "assets": [{"name": name, "browser_download_url": base + name} for name in names],
    }


class UpdaterTests(unittest.TestCase):
    def test_versions_are_numeric_and_strict(self):
        self.assertLess(updates.version_tuple("0.2.9"), updates.version_tuple("0.2.10"))
        for value in ["v1.0.0", "1.0.0-beta", "1.0", "../1.0.0"]:
            with self.subTest(version=value), self.assertRaises(updates.UpdateError):
                updates.version_tuple(value)

    def test_only_newer_stable_desktop_release_selected(self):
        payload = [
            release("0.3.1", draft=True),
            release("0.4.0", prerelease=True),
            release("0.5.0", assets=False),
            release("0.1.9"),
            release("0.3.0"),
            release("0.2.2"),
        ]
        with mock.patch.object(updates, "_get", return_value=json.dumps(payload).encode()):
            selected = updates.find_update("0.2.0")
            self.assertEqual(selected["version"], "0.3.0")
            self.assertIsNone(updates.find_update("0.3.0"))

    def test_rejects_cross_repository_download(self):
        with self.assertRaises(updates.UpdateError):
            updates._validate_asset(
                {"browser_download_url": "https://example.org/PanDoc-Setup-Windows-x64.exe"},
                name=updates.ASSET_NAME,
            )

    def test_checksummed_installer_is_only_promoted_when_valid(self):
        binary = b"MZ" + b"test" * 320_000
        checksum = hashlib.sha256(binary).hexdigest()
        urls = release("0.3.0")["assets"]
        metadata = {
            "version": "0.3.0",
            "installer_url": urls[0]["browser_download_url"],
            "checksum_url": urls[1]["browser_download_url"],
        }
        fake_checksum = f"{checksum}  {updates.ASSET_NAME}\n".encode()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with (mock.patch.object(updates, "_get", return_value=fake_checksum),
                  mock.patch.object(updates.urllib.request, "urlopen", return_value=io.BytesIO(binary))):
                result = updates.download_verified(metadata, directory)
                self.assertEqual(result.read_bytes(), binary)
                self.assertEqual(metadata["verified_sha256"], checksum)
                self.assertFalse(list(directory.glob("*.partial")))
            with (mock.patch.object(updates, "_get", return_value=b"0" * 64 + b"  " + updates.ASSET_NAME.encode()),
                  mock.patch.object(updates.urllib.request, "urlopen", return_value=io.BytesIO(binary))):
                with self.assertRaisesRegex(updates.UpdateError, "SHA-256 mismatch"):
                    updates.download_verified(metadata, directory)
                self.assertFalse(list(directory.glob("*.partial")))

    def test_installer_rejects_unverified_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            verified_dir = base / "updates"
            verified_dir.mkdir()
            other = base / "elsewhere.exe"
            other.write_bytes(b"fake")
            with (mock.patch.object(updates.sys, "platform", "win32"),
                  mock.patch.object(updates.sys, "frozen", True, create=True)):
                with self.assertRaises(updates.UpdateError):
                    updates.schedule_install(other, other, verified_dir, "0" * 64)


if __name__ == "__main__":
    unittest.main()
