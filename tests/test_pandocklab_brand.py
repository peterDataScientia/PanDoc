"""Guard PanDockLab public branding without breaking installed PanDoc clients."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanDockLabBrandTests(unittest.TestCase):
    def test_interface_and_sidebar(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("page_title='PanDockLab · Docking workbench'", app)
        self.assertIn('aria-label="PanDockLab"', app)
        self.assertIn("initial_sidebar_state='expanded' if", app)

    def test_upgrade_installer_identity_is_unchanged(self):
        installer = (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn('#define MyAppName "PanDockLab"', installer)
        self.assertIn("AppId={{A91B9777-8E67-47BE-A712-7C23418C5275}", installer)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\PanDoc", installer)
        self.assertIn('#define MyAppExeName "PanDoc.exe"', installer)
        self.assertIn('Name: "{group}\\PanDockLab"', installer)
        self.assertIn("OutputBaseFilename=PanDoc-Setup-Windows-x64", installer)

    def test_updater_legacy_discovery_remains_supported(self):
        from pandoc import desktop_updater as updater
        self.assertEqual(updater.REPOSITORY, "peterDataScientia/PanDoc")
        self.assertEqual(updater.TAG_PREFIX, "pandoc-desktop-v")
        self.assertEqual(updater.ASSET_NAME, "PanDoc-Setup-Windows-x64.exe")
        self.assertEqual(updater.version_tuple("0.3.0"), (0, 3, 0))

    def test_release_pipeline_has_both_names(self):
        flow = (ROOT / ".github" / "workflows" /
                "build-windows-desktop.yml").read_text(encoding="utf-8")
        self.assertIn("PanDockLab-Setup-Windows-x64.exe", flow)
        self.assertIn("PanDoc-Setup-Windows-x64.exe", flow)
        self.assertIn("PanDockLab Desktop v$version", flow)
        self.assertEqual((ROOT / "desktop" / "VERSION").read_text().strip(), "0.3.0")


if __name__ == "__main__":
    unittest.main()
