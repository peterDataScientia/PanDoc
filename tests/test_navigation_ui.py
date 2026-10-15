"""Workflow navigation stays in the sidebar; main workspace has no redundant headings/cards."""

from pathlib import Path
from pandoc import ui

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
CSS = (ROOT / "assets" / "pandoc.css").read_text(encoding="utf-8")


class Capture:
    def __init__(self):
        self.html = ""

    def markdown(self, content, **kwargs):
        self.html = content


def test_main_workspace_does_not_render_duplicate_stage_heading_or_status_grid():
    assert "ui.current_stage_header(st, stage)" not in APP
    assert "ui.status_grid(st," not in APP
    assert not hasattr(ui, "current_stage_header")
    assert not hasattr(ui, "status_grid")
    assert "ui.scroll_anchor(st, 'pd-stage')" in APP


def test_stage_scroll_anchor_has_no_visual_step_counter():
    capture = Capture()
    ui.scroll_anchor(capture, "pd-stage")
    assert 'id="pd-stage"' in capture.html
    assert "STEP 1 OF 5" not in capture.html
    assert "Load complex" not in capture.html
    assert "pd-status" not in capture.html


def test_sidebar_navigation_remains_and_main_top_spacing_is_compact():
    assert "with st.container(key='workflow_navigation')" in APP
    assert "on_click=select_workflow_from_menu" in APP
    assert '[data-testid="stExpandSidebarButton"]' in CSS
    assert 'content:"Menu"' in CSS
    assert 'padding-top:.9rem!important' in CSS
    assert '.pd-sidebar-workflow-title' in CSS


def test_compute_runtime_is_a_top_right_popover_not_a_sidebar_radio():
    assert "with st.container(key='runtime_toolbar')" in APP
    assert "with st.popover('Runtime', icon=':material/memory:'" in APP
    assert "format_func=compute_options.runtime_label" in APP
    assert "key='compute_mode'" in APP
    assert "st.radio(\n        'Where calculations run'," not in APP
    assert "st.session_state.get('compute_mode'" in APP
    assert ".st-key-runtime_toolbar" in CSS


def test_molecular_viewer_uses_chain_palette_instead_of_one_cartoon_color():
    assert "structure_view.style_protein_chains(view, pdb)" in APP
    assert "structure_view.chain_legend(chain_colors)" in APP
    assert "color': '#7b94ad'" not in APP
