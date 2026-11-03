"""Deterministic, chain-aware colors for PanDoc's protein structure viewer.

Only polymer ATOM records determine chain colors. Retained cofactors,
solvent and ligand models keep their separate stick representations.
"""
from __future__ import annotations

import colorsys
import html

from . import core

# Saturated, visually distinct hues; first entries remain stable across views.
CHAIN_PALETTE = (
    "#0072B2", "#D55E00", "#009E73", "#CC79A7",
    "#E69F00", "#7B61A8", "#56B4E9", "#A65E2E",
    "#228B69", "#C44569", "#866D20", "#396B92",
)
FALLBACK_CARTOON_COLOR = "#607D95"


def protein_chain_colors(pdb_text: str) -> list[tuple[str, str]]:
    """Map first-model protein chain IDs to colors, independent of atom order."""
    chains = sorted({
        atom["chain"]
        for atom in core.atoms(pdb_text)
        if atom["record"] == "ATOM"
    })
    result = []
    for index, chain in enumerate(chains):
        if index < len(CHAIN_PALETTE):
            color = CHAIN_PALETTE[index]
        else:
            # Golden-angle hues avoid repeating colors for large assemblies.
            r, g, b = colorsys.hsv_to_rgb((index * 0.61803398875) % 1.0, 0.75, 0.88)
            color = f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"
        result.append((chain, color))
    return result


def style_protein_chains(view, pdb_text: str, model: int = 0) -> list[tuple[str, str]]:
    """Apply per-chain cartoon colors without touching HETATM/ligand styles."""
    colors = protein_chain_colors(pdb_text)
    view.setStyle({"model": model}, {"cartoon": {"color": FALLBACK_CARTOON_COLOR}})
    for chain, color in colors:
        view.setStyle({"model": model, "chain": chain, "hetflag": False},
                      {"cartoon": {"color": color}})
    return colors


def chain_legend(colors: list[tuple[str, str]]) -> str:
    """Small self-contained HTML legend using precisely the rendered colors."""
    if not colors:
        return ""
    swatches = "".join(
        '<span style="display:inline-flex;align-items:center;gap:5px;'
        'font:600 12px system-ui;color:#24364a;white-space:nowrap">'
        f'<span style="display:inline-block;width:12px;height:12px;border-radius:4px;'
        f'background:{color};border:1px solid #64748b"></span>'
        f'Chain {html.escape(chain) if chain else "(unassigned)"}'
        '</span>'
        for chain, color in colors
    )
    return ('<div aria-label="Protein chain colors" style="display:flex;gap:12px;'
            'flex-wrap:wrap;align-items:center;padding:4px 8px 8px">'
            '<span style="font:700 12px system-ui;color:#334155">Protein chains:</span>'
            + swatches + '</div>')


def docking_box_pocket_residues(pdb_text: str, center, size):
    """Protein residues intersecting the Vina box, retaining chain/insertion IDs.

    This follows Anyone Can Dock's box-bounded pocket selection without
    accidentally merging identical residue numbers across protein chains.
    """
    from math import isfinite
    c = [float(v) for v in center]
    d = [float(v) for v in size]
    if len(c) != 3 or len(d) != 3 or not all(map(isfinite, c + d)) or any(v <= 0 for v in d):
        raise ValueError("Docking box needs three finite coordinates and positive dimensions.")
    lo = [c[i] - d[i] / 2 for i in range(3)]
    hi = [c[i] + d[i] / 2 for i in range(3)]
    return {
        (a["chain"], a["number"], a["icode"])
        for a in core.atoms(pdb_text)
        if a["record"] == "ATOM"
        and all(lo[i] <= a["xyz"][i] <= hi[i] for i in range(3))
    }


def style_docking_pocket(view, pdb_text: str, center, size, model: int = 0):
    """Highlight complete protein residues intersecting the grid box."""
    residues = docking_box_pocket_residues(pdb_text, center, size)
    # Explicit chain-aware selections prevent cross-chain false pocket residues.
    for chain, number, icode in sorted(residues):
        selector = {"model": model, "chain": chain, "resi": int(number), "hetflag": False}
        if icode:
            selector["icode"] = icode
        view.addStyle(selector, {
            "stick": {"colorscheme": "whiteCarbon", "radius": 0.18},
        })
    return residues


def draw_docking_box_frame(view, center, size, *, radius=0.13):
    """Draw 12 solid gray box edges, matching the ACD receptor-setup frame.

    The geometry is presentation-only; the docking search space is unchanged.
    Uses cylinders because py3Dmol's built-in wireframe can be too thin.
    """
    from math import isfinite
    c = [float(v) for v in center]
    d = [float(v) for v in size]
    if len(c) != 3 or len(d) != 3 or not all(map(isfinite, c + d)) or any(v <= 0 for v in d):
        raise ValueError("Docking box needs three finite coordinates and positive dimensions.")
    half = [v / 2 for v in d]
    vertices = [
        {axis: c[i] + (half[i] if bits & (1 << i) else -half[i])
         for i, axis in enumerate("xyz")}
        for bits in range(8)
    ]
    for i in range(8):
        for axis in (0, 1, 2):
            j = i ^ (1 << axis)
            if i < j:
                view.addCylinder({
                    "start": vertices[i], "end": vertices[j],
                    "radius": float(radius), "color": "#666666",
                    "fromCap": 1, "toCap": 1,
                })
    return vertices


def ligand_pocket_residues(pdb_text: str, ligand_coordinates, cutoff: float = 3.5):
    """Chain- and insertion-aware protein residues near ligand heavy atoms.

    Proximity alone is not an assignment of hydrogen bonds or other interactions.
    """
    from math import isfinite
    radius = float(cutoff)
    if not isfinite(radius) or radius <= 0:
        raise ValueError("Pocket cutoff must be a positive finite distance in angstroms.")
    coords = [tuple(float(v) for v in point) for point in ligand_coordinates]
    if any(len(p) != 3 or not all(map(isfinite, p)) for p in coords):
        raise ValueError("Ligand coordinates must be finite XYZ triplets.")
    if not coords:
        return set()
    squared = radius * radius
    return {
        (a["chain"], a["number"], a["icode"], a["res"])
        for a in core.atoms(pdb_text)
        if a["record"] == "ATOM" and a["element"] not in ("H", "D")
        and any(sum((a["xyz"][i] - p[i]) ** 2 for i in range(3)) <= squared
                for p in coords)
    }


def style_pose_pocket(view, pdb_text, ligand_coordinates, cutoff=3.5, *, labels=False):
    """Anyone Can Dock-style orange pocket sticks and optional yellow labels."""
    residues = ligand_pocket_residues(pdb_text, ligand_coordinates, cutoff)
    for chain, number, icode, resname in sorted(residues):
        selection = {"model": 0, "chain": chain, "resi": int(number), "hetflag": False}
        if icode:
            selection["icode"] = icode
        view.addStyle(selection, {
            "stick": {"colorscheme": "orangeCarbon", "radius": 0.20}
        })
        if labels:
            view.addLabel(f"{resname}{number}{icode}{chain}", {
                "fontSize": 11, "fontColor": "yellow",
                "backgroundColor": "black", "backgroundOpacity": 0.65,
                "inFront": True, "showBackground": True,
            }, selection)
    return residues
