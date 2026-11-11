"""Guard PanDockLab public branding without breaking installed PanDoc clients."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanDockLabBrandTests(unittest.TestCase):
    def test_interface_and_sidebar(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("page_title='PanDockLab · Docking workbench'", app)
        self.assertIn('alt="PanDockLab scientific logo"', app)
        self.assertIn("pandocklab_selected_logo.webp.b64", app)
        self.assertIn("page_icon=_pandocklab_icon()", app)
        self.assertIn("initial_sidebar_state='expanded' if", app)

    def test_desktop_shows_top_right_logo_only(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('_brand_logo(width=85, floating=True)', app)
        self.assertIn('if os.environ.get("PANDOC_DESKTOP_APP") != "1":\n        _brand_logo(width=250)', app)
        self.assertIn('with st.sidebar:', app)

    def test_desktop_sidebar_can_reopen_after_collapse(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('header[data-testid="stHeader"]', app)
        self.assertIn('[data-testid="stExpandSidebarButton"]', app)
        self.assertIn('[data-testid="stSidebarCollapsedControl"]', app)
        self.assertIn('pointer-events: auto !important', app)

    def test_local_docking_log_reports_measured_completion_percentage(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        worker = (ROOT / "pandoc" / "worker.py").read_text(encoding="utf-8")
        self.assertIn('Docking completed: {percent:.1f}%', app)
        self.assertIn('DOCKING_PROGRESS 0/{total} (0.0%)', worker)
        self.assertIn('DOCKING_PROGRESS {completed_searches}/{total}', worker)
        self.assertIn('Vina does not provide a validated continuous percentage', app)

    def test_incomparable_reference_does_not_terminate_docking(self):
        worker = (ROOT / "pandoc" / "worker.py").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('row["rmsd_validation_status"] = "not_comparable"', worker)
        self.assertIn('row["rmsd_warning"] = str(exc)', worker)
        self.assertIn('except ValueError as exc:', worker)
        self.assertIn('row["rmsd_validation_status"] = "valid"', worker)
        self.assertIn('No chemically comparable reference RMSD is available', app)
        self.assertIn('excluded poses are not successes', app)
        self.assertIn("df.dropna(subset=['reference_rmsd_A'])", app)

    def test_docking_log_clipboard_and_download_controls(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("def render_docking_log_controls(log_text", app)
        self.assertIn('["clip.exe"]', app)
        self.assertIn('input=log_text.encode("utf-16")', app)
        self.assertIn('"Copy log"', app)
        self.assertIn('"Download log (.txt)"', app)
        self.assertIn('key="remote_log_" + remote', app)
        self.assertIn('key="local_log_" + Path(directory).name', app)
        self.assertIn('log.read_text(errors=\'replace\')', app)

    def test_desktop_updater_uses_compact_collapsible_sidebar(self):
        code = (ROOT / "pandoc" / "desktop_updater.py").read_text(encoding="utf-8")
        self.assertIn('st.sidebar.expander("Software updates", expanded=False)', code)
        self.assertNotIn("st.sidebar.container(border=True)", code)
        self.assertIn('"Check for updates"', code)
        self.assertIn('"Download and install update"', code)

    def test_windows_icon_derived_from_selected_logo(self):
        code = (ROOT / "desktop" / "build_icon.py").read_text(encoding="utf-8")
        self.assertIn("pandocklab_selected_logo.webp.b64", code)
        self.assertIn("base64.b64decode", code)
        self.assertIn("pandocklab.ico", code)
        self.assertIn("--icon desktop/vendor/pandocklab.ico",
                      (ROOT / ".github" / "workflows" /
                       "build-windows-desktop.yml").read_text(encoding="utf-8"))

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
        self.assertEqual((ROOT / "desktop" / "VERSION").read_text().strip(), "0.3.4")


if __name__ == "__main__":
    unittest.main()
