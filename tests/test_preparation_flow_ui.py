"""Guard PanDoc's single-direction receptor/ligand preparation UI.

These checks protect the control order and Streamlit callback against regressions
without invoking molecular preparation or external backend jobs.
"""
import ast
from pathlib import Path


APP_SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_app_compiles_and_has_linear_preparation_flow():
    ast.parse(APP_SOURCE)
    assert "protein_tab, ligand_tab = st.tabs" not in APP_SOURCE
    assert "key='preparation_view'" not in APP_SOURCE
    assert "preparation_view = st.radio(" not in APP_SOURCE
    assert "st.markdown('#### 4. Reference ligand preparation')" in APP_SOURCE
    assert "Next: prepare the crystallographic reference ligand below." in APP_SOURCE


def test_protonation_pH_is_visible_before_analysis():
    begin = APP_SOURCE.index("ui.card(st, 'Receptor preparation'")
    pH = APP_SOURCE.index("'Preparation pH'", begin)
    optional = APP_SOURCE.index("with st.expander('Optional receptor settings'", begin)
    analyze = APP_SOURCE.index("if st.button('Analyze protonation at selected pH'", begin)
    prep = APP_SOURCE.index("if st.button('Prepare receptor'", begin)
    assert begin < pH < optional < analyze < prep


def test_receptor_review_is_not_a_large_default_preview():
    assert "if st.toggle('Inspect input receptor · structure report and 3D view'" in APP_SOURCE
    assert "if st.toggle('Inspect prepared receptor · 3D, downloads and report'" in APP_SOURCE
    assert "key='show_input_receptor_review_'" in APP_SOURCE


def test_receptor_and_ligand_are_sequential_in_one_workspace():
    receptor = APP_SOURCE.index("ui.card(st, 'Receptor preparation'")
    ligand = APP_SOURCE.index("st.markdown('#### 4. Reference ligand preparation')", receptor)
    assert receptor < ligand
    assert "go_to_reference_preparation" not in APP_SOURCE
    assert "st.button('Continue to Validate docking →'" in APP_SOURCE


def test_ligand_continuation_precedes_optional_3D_preview():
    start = APP_SOURCE.index("if st.session_state.get('reference_path'):", APP_SOURCE.index("st.markdown('### Reference ligand: '"))
    continue_pos = APP_SOURCE.index("st.button('Continue to Validate docking →'", start)
    preview_pos = APP_SOURCE.index("if st.toggle('Inspect prepared ligand · 3D preview and download'", start)
    assert start < continue_pos < preview_pos



def test_workflow_menu_cannot_overwrite_stage_during_preparation():
    assert "def select_workflow_from_menu():" in APP_SOURCE
    assert "st.session_state.workflow_stage = st.session_state.workflow_menu_choice" in APP_SOURCE
    assert "st.session_state.workflow_menu_choice = st.session_state.workflow_stage" in APP_SOURCE
    assert "key='workflow_menu_choice'" in APP_SOURCE
    assert "on_change=select_workflow_from_menu" in APP_SOURCE
    assert "stage = st.session_state.workflow_stage" in APP_SOURCE
    assert "key='workflow_stage'" not in APP_SOURCE
