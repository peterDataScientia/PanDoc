"""Accessibility regression checks for PanDoc's light visual theme.

The checks target the design tokens, not generated Streamlit CSS class names,
so they remain stable as the framework updates.
"""
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "assets" / "pandoc.css").read_text(encoding="utf-8")
THEME = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]


def rgb(color):
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", color)
    return [int(color[i:i+2], 16) / 255.0 for i in (1, 3, 5)]


def luminance(color):
    components = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in rgb(color)
    ]
    return .2126 * components[0] + .7152 * components[1] + .0722 * components[2]


def ratio(a, b):
    dark, light = sorted((luminance(a), luminance(b)))
    return (light + .05) / (dark + .05)


def token(name):
    match = re.search(rf"--pd-{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", CSS)
    assert match is not None, f"Missing PanDoc token {name}"
    return match.group(1)


def test_text_and_muted_labels_remain_legible():
    assert ratio(token("text"), token("surface")) >= 7.0
    assert ratio(token("muted"), token("surface")) >= 4.5
    assert ratio(token("text"), token("bg")) >= 7.0
    assert ratio(token("muted"), token("bg")) >= 4.5
    assert ratio("#ffffff", token("navy")) >= 7.0
    assert ratio("#ffffff", token("teal")) >= 4.5


def test_native_streamlit_theme_matches_custom_pan_doc_palette():
    assert THEME["base"] == "light"
    assert THEME["backgroundColor"] == token("bg")
    assert THEME["textColor"] == token("text")
    assert THEME["primaryColor"] == token("teal")
    assert ratio(THEME["textColor"], THEME["backgroundColor"]) >= 7.0
    assert ratio("#ffffff", THEME["primaryColor"]) >= 4.5


def test_component_styles_have_specific_focus_and_readable_header():
    assert "button:focus-visible" in CSS
    assert "outline:3px solid" in CSS
    assert "pd-shell-title { color:#fff;" in CSS
    assert ".pd-card-title" in CSS
    assert ".pd-step.active" in CSS
    assert '.pd-automation-marker{' in CSS
    assert ".pd-badge.neutral" in CSS
