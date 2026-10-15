"""Navigation presentation tests: a single, collapsed sidebar menu."""

from pathlib import Path
from pandoc import ui


class Capture:
    def __init__(self):
        self.html = ""

    def markdown(self, content, **kwargs):
        self.html = content


def test_main_heading_only_shows_active_step():
    capture = Capture()
    ui.current_stage_header(capture, "3 · Validate docking")
    assert "STEP 3 OF 5" in capture.html
    assert "Validate docking" in capture.html
    assert "Load complex" not in capture.html
    assert "Run experiment" not in capture.html
    assert 'class="pd-stepper"' not in capture.html


def test_current_step_title_is_escaped():
    capture = Capture()
    ui.current_stage_header(capture, "2 · <script>alert(1)</script>")
    assert "&lt;script&gt;" in capture.html
    assert "<script>" not in capture.html


def test_native_sidebar_button_has_visible_menu_label():
    css = (Path(__file__).resolve().parents[1] / "assets" / "pandoc.css").read_text(encoding="utf-8")
    assert '[data-testid="stSidebarCollapsedControl"] button::after' in css
    assert 'content:"☰  Menu"' in css
    assert ".pd-workspace-title" in css
