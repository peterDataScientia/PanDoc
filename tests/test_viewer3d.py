"""Regression checks for molecular viewer markup and visible error recovery."""
import json
import re

from pandoc.viewer3d import viewer_html


def minimal_pdb():
    return (
        "ATOM      1  N   GLY A   1      12.000  13.000  14.000  1.00 20.00           N  \n"
        "ATOM      2  CA  GLY A   1      13.000  13.000  14.000  1.00 20.00           C  \n"
        "END\n"
    )


def test_protein_viewer_has_real_coordinates_and_bootstrap():
    html = viewer_html(pdb=minimal_pdb())
    assert 'cdn.jsdelivr.net' in html
    assert 'unpkg.com' in html
    assert 'tag.onerror' in html
    assert 'WebGL is unavailable' in html
    assert 'No atoms parsed' in html
    assert 'Retry viewer' in html
    assert 'Zoom +' in html
    assert 'GLY A' in html
    assert 'viewer.render()' in html
    assert 'ResizeObserver' in html


def test_different_viewers_use_distinct_element_ids():
    first = viewer_html(pdb=minimal_pdb())
    second = viewer_html(pdb=minimal_pdb())
    first_id = re.search(r'(<div id=")(pandoc3d_[0-9a-f]+)_scene', first)
    second_id = re.search(r'(<div id=")(pandoc3d_[0-9a-f]+)_scene', second)
    assert first_id and second_id
    assert first_id.group(2) != second_id.group(2)


def test_focus_box_and_both_ligands():
    sdf = "mol\n  test\n\n  0  0  0  0  0  0  0  0  0  0  0 V2000\nM  END\n$$$$"
    html = viewer_html(
        pdb=minimal_pdb(),
        reference=sdf,
        sdf=sdf,
        center=[1, 2, 3],
        size=[8, 9, 10],
        focus="A:1:GLY",
    )
    assert '"kind": "reference"' in html
    assert '"kind": "candidate"' in html
    assert '"focus_serials": [1, 2]' in html
    assert '"dimensions": {"w": 8.0, "h": 9.0, "d": 10.0}' in html


def test_script_escaping_prevents_untrusted_structure_html_injection():
    evil = "sdf\n</script><img src=x onerror=alert(1)>\n"
    html = viewer_html(sdf=evil)
    assert '</script><img' not in html
    assert '\\u003c/script\\u003e' in html


def test_empty_viewer_explains_problem():
    assert "No structure supplied" in viewer_html()
