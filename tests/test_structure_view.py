"""Regression tests for distinct protein-chain colors and viewer legend."""
from pandoc import structure_view, viewer_runtime
import py3Dmol


def atom(serial, chain, record="ATOM  ", residue="ALA"):
    return (
        f"{record}{serial:5d} {'CA':>4s} {residue:3s} {chain:1s}{1:4d}    "
        f"{float(serial):8.3f}{0.:8.3f}{0.:8.3f}"
        f"{1.:6.2f}{20.:6.2f}          C  "
    )


class Fake3DView:
    def __init__(self):
        self.styles = []

    def setStyle(self, selection, style):
        self.styles.append((selection, style))


def test_distinct_chain_colors_are_stable_and_ignore_nonprotein_components():
    pdb = "\n".join([
        atom(1, "C"), atom(2, "A"), atom(3, "B"),
        atom(4, "Z", record="HETATM", residue="LIG"),
    ])
    colors = structure_view.protein_chain_colors(pdb)
    assert [chain for chain, _ in colors] == ["A", "B", "C"]
    assert len({color for _, color in colors}) == 3
    assert colors == structure_view.protein_chain_colors("\n".join(pdb.splitlines()[::-1]))
    assert "Z" not in dict(colors)


def test_chain_cartoon_styles_and_legend_use_the_same_colors():
    pdb = "\n".join([atom(1, "A"), atom(2, "B")])
    view = Fake3DView()
    colors = structure_view.style_protein_chains(view, pdb, model=2)
    assert view.styles[0] == (
        {"model": 2}, {"cartoon": {"color": structure_view.FALLBACK_CARTOON_COLOR}}
    )
    for (chain, color), (selector, representation) in zip(colors, view.styles[1:]):
        assert selector == {"model": 2, "chain": chain, "hetflag": False}
        assert representation == {"cartoon": {"color": color}}
    legend = structure_view.chain_legend(colors)
    assert 'Protein chains:' in legend
    for chain, color in colors:
        assert f"Chain {chain}" in legend
        assert f"background:{color}" in legend


def test_missing_chain_id_and_large_assemblies_are_handled():
    pdb = "\n".join([atom(1, " "), atom(2, "A")])
    colors = structure_view.protein_chain_colors(pdb)
    assert colors[0][0] == ""
    assert "(unassigned)" in structure_view.chain_legend(colors)
    # Additional distinct colors are generated after the fixed palette.
    ids = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    big = "\n".join(atom(i + 1, ch) for i, ch in enumerate(ids))
    palette = structure_view.protein_chain_colors(big)
    assert len(palette) == len(ids)
    assert len(set(dict(palette).values())) == len(ids)



def test_desktop_viewer_tries_backup_3dmol_sources_and_reports_failure():
    view = py3Dmol.view(width="100%", height=430)
    view.addModel(atom(1, "A") + "\n" + atom(2, "B"), "pdb")
    view.setStyle({"chain": "A"}, {"cartoon": {"color": "#1356ae"}})
    view.zoomTo()
    html = viewer_runtime.render_viewer_html(
        view, controls="<button>Zoom +</button>", legend="<div>Chain A</div>"
    )
    assert "https://3Dmol.org/build/3Dmol-min.js" in html
    assert "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.5.5/3Dmol-min.js" in html
    assert "script.onerror" in html
    assert "setTimeout(function()" in html
    assert "return attempt(index + 1)" in html
    assert "3D view unavailable:" in html
    assert 'role="alert"' in html
    assert "ResizeObserver" in html
    assert "Zoom +" in html
    assert "Chain A" in html
    assert "viewer_" + view.uniqueid in html
    assert ".addModel(" in html
    assert ".setStyle(" in html
    assert ".zoomTo(" in html
    assert ".render(" in html
    assert html.count("3dmolviewer_" + view.uniqueid) >= 2


def test_py3dmol_scene_wrapper_never_silently_drops_loader_contract():
    class UnsupportedVersion:
        uniqueid = "1234"

        def _make_html(self):
            return '<div id="fake"></div>'

    try:
        viewer_runtime.render_viewer_html(UnsupportedVersion())
    except RuntimeError as exc:
        assert "loader template changed" in str(exc)
    else:
        raise AssertionError("Unsupported py3Dmol HTML must fail explicitly")


def test_docking_pocket_selects_box_intersection_without_merging_chains():
    def near_atom(serial, chain, residue_number, x):
        line = atom(serial, chain)
        return line[:22] + f"{residue_number:4d}" + line[26:30] + f"{x:8.3f}" + line[38:]
    pdb = "\n".join([
        near_atom(1, "A", 10, 0.0),
        near_atom(2, "B", 10, 40.0),
        near_atom(3, "A", 20, 1.0),
    ])
    selected = structure_view.docking_box_pocket_residues(
        pdb, [0, 0, 0], [10, 10, 10]
    )
    assert selected == {("A", "10", ""), ("A", "20", "")}


def test_docking_pocket_styles_only_selected_protein_residues():
    pdb = atom(1, "A") + "\n" + atom(2, "B")
    view = Fake3DView()
    view.addStyle = lambda selector, style: view.styles.append((selector, style))
    result = structure_view.style_docking_pocket(
        view, pdb, [1, 0, 0], [10, 10, 10], model=0
    )
    assert len(result) == 2
    assert len(view.styles) == 2
    assert {entry[0]["chain"] for entry in view.styles} == {"A", "B"}
    assert all(entry[1]["stick"]["colorscheme"] == "whiteCarbon"
               for entry in view.styles)


def test_docking_box_gray_frame_has_twelve_edges_and_correct_corners():
    class Frame:
        def __init__(self):
            self.cylinders = []
        def addCylinder(self, options):
            self.cylinders.append(options)

    view = Frame()
    vertices = structure_view.draw_docking_box_frame(view, [10, 20, 30], [12, 18, 24])
    assert len(vertices) == 8
    assert len(view.cylinders) == 12
    assert vertices[0] == {"x": 4.0, "y": 11.0, "z": 18.0}
    assert vertices[-1] == {"x": 16.0, "y": 29.0, "z": 42.0}
    assert all(edge["color"] == "#666666" and edge["radius"] == 0.13
               for edge in view.cylinders)
