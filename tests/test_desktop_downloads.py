"""Regression checks for downloads in the frozen Windows pywebview launcher.

These tests have no GUI requirement; real Save As behavior still needs a
Windows WebView2 smoke test after building the installer.
"""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "desktop" / "launcher.py"


class DesktopDownloadTests(unittest.TestCase):
    def test_downloads_explicitly_enabled_before_gui_start(self):
        tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        enabled_at = []
        started_at = []
        created_at = []
        for node in ast.walk(main):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Attribute)
                            and isinstance(target.value.value, ast.Name)
                            and target.value.value.id == "webview"
                            and target.value.attr == "settings"
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "ALLOW_DOWNLOADS"
                            and isinstance(node.value, ast.Constant)
                            and node.value.value is True):
                        enabled_at.append(node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "webview":
                    if node.func.attr == "start":
                        started_at.append(node.lineno)
                    if node.func.attr == "create_window":
                        created_at.append(node.lineno)
        self.assertEqual(len(enabled_at), 1, "Enable native WebView2 downloads exactly once")
        self.assertEqual(len(started_at), 1)
        self.assertEqual(len(created_at), 1)
        self.assertLess(enabled_at[0], created_at[0])
        self.assertLess(enabled_at[0], started_at[0])

    def test_embedded_edgechromium_renderer_is_retained(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('webview.start(gui="edgechromium")', text)


if __name__ == "__main__":
    unittest.main()
