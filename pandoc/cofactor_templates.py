"""Conservative, auditable fallback for Meeko CCD residue-template failures.

The CCD ideal SDF is used for *chemical connectivity only*: PanDoc never
replaces crystallographic receptor coordinates with CCD ideal coordinates.
Complexes containing metals or covalent attachments require curated chemistry.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re

METALS = frozenset(
    "LI NA K RB CS MG CA SR BA MN FE CO NI CU ZN CD HG AL CR V MO W PT PD AG AU".split()
)
CCD_BASE = "https://files.rcsb.org/ligands/download"


def component_groups(pdb):
    """Return deposited, non-polymer components with their original atom records."""
    from . import core
    groups = {}
    for atom in core.atoms(pdb):
        name = atom["res"].upper()
        if name in core.STANDARD or name in core.WATERS:
            continue
        groups.setdefault(name, {}).setdefault(core.key(atom), []).append(atom)
    return groups


def _metal_or_linked(name, group, pdb):
    if any(a["element"].upper() in METALS for atoms in group.values() for a in atoms):
        return "a metal-containing chemical component"
    for line in pdb.splitlines():
        if line.startswith("LINK  ") and name in (line[17:20].strip().upper(),
                                                    line[47:50].strip().upper()):
            return "an explicitly linked/covalent chemical component"
    return None


def failing_components(log, candidates):
    """Only retry when the Meeko diagnostic unambiguously names a component."""
    patterns = (
        r"template\s+for\s+([A-Z0-9]{1,3})\b",
        r"resname\s*=\s*['\"]?([A-Z0-9]{1,3})\b",
        r"explicit Meeko template for\s+([A-Z0-9]{1,3})\b",
    )
    names = {name for pattern in patterns for name in re.findall(pattern, log, re.I)}
    matched = sorted({name.upper() for name in names} & set(candidates))
    if matched:
        return matched
    # Avoid attempting arbitrary downloads for failures unrelated to CCD.
    if "ccd" in log.lower() and len(candidates) == 1:
        return list(candidates)
    return []


def validate_sdf(sdf, instances):
    """Require a single readable chemical graph with identical heavy-element inventory."""
    from rdkit import Chem
    if not sdf or len(sdf) > 3_000_000:
        raise ValueError("CCD SDF is empty or exceeds the 3 MB component limit.")
    mols = list(Chem.ForwardSDMolSupplier(io.BytesIO(sdf), removeHs=False))
    mols = [mol for mol in mols if mol is not None]
    if len(mols) != 1:
        raise ValueError("CCD SDF must contain exactly one chemically valid molecule.")
    mol = mols[0]
    Chem.SanitizeMol(mol)
    if len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError("CCD SDF has disconnected chemical fragments.")
    observed = Counter(atom.GetSymbol().upper() for atom in mol.GetAtoms()
                       if atom.GetAtomicNum() > 1)
    for residue, pdb_atoms in instances.items():
        expected = Counter(a["element"].upper() for a in pdb_atoms
                           if a["element"].upper() not in ("H", "D"))
        if observed != expected:
            raise ValueError(
                f"{residue}: CCD and receptor heavy-element inventories differ "
                f"(CCD={dict(observed)}, deposited={dict(expected)})."
            )
    if any(atom.GetSymbol().upper() in METALS for atom in mol.GetAtoms()):
        raise ValueError("Metal-containing CCD SDF requires a curated template.")
    return mol


def _template_for_component(name, group, pdb, directory):
    """Prefer vetted local templates; download only for ordinary noncovalent CCD ligands."""
    reason = _metal_or_linked(name, group, pdb)
    if reason:
        raise ValueError(f"{name} is {reason}; automatic SDF chemistry is unsafe.")
    if not re.fullmatch(r"[A-Z0-9]{1,3}", name):
        raise ValueError(f"Invalid CCD component ID {name!r}.")
    source = os.environ.get("PANDOC_COF_TEMPLATE_DIR", "").strip()
    if source:
        base = Path(source).expanduser()
        json_template = base / f"{name}.json"
        if json_template.is_file():
            # Explicitly curated JSON; Meeko validates it when loading.
            return str(json_template), "curated-json"
        sdf_template = base / f"{name}.sdf"
        if sdf_template.is_file():
            validate_sdf(sdf_template.read_bytes(), group)
            return f"{name}:{sdf_template}", "curated-sdf"

    cache = Path(
        os.environ.get("PANDOC_COF_CACHE_DIR") or
        (Path(directory).parent / "_ccd_template_cache")
    ).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{name}_ideal.sdf"
    if path.is_file():
        try:
            validate_sdf(path.read_bytes(), group)
            return f"{name}:{path}", "validated-cache"
        except (ValueError, OSError):
            path.unlink(missing_ok=True)

    # RCSB stopped distributing *_model.sdf in 2024. *_ideal.sdf
    # remains supported. It supplies topology only, never receptor coordinates.
    import requests
    url = f"{CCD_BASE}/{name}_ideal.sdf"
    response = requests.get(url, timeout=12)
    response.raise_for_status()
    data = response.content
    validate_sdf(data, group)
    path.write_bytes(data)
    return f"{name}:{path}", url


def resolve_ccd_failure(pdb, directory, first_log, existing_templates=()):
    """Return additional --add_templates values and an auditable status record."""
    directory = Path(directory)
    groups = component_groups(pdb)
    failed = failing_components(first_log, groups)
    attempted = []
    found = []
    existing = [str(x) for x in existing_templates]
    for name in failed:
        record = {"component": name, "residues": sorted(groups[name])}
        attempted.append(record)
        if any(item.startswith(name + ":") or Path(item).stem.upper() == name
               for item in existing):
            record.update(status="failed", reason="an explicit template was already supplied")
            continue
        try:
            template, source = _template_for_component(name, groups[name], pdb, directory)
            found.append(template)
            record.update(status="candidate", provenance=source,
                          template_sha256=hashlib.sha256(
                              Path(template[len(name) + 1:] if template.startswith(name + ":") else template).read_bytes()
                          ).hexdigest())
        except Exception as exc:
            # Never obscure the original Meeko error; preserve this reason in the audit.
            record.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    report = {"stage": "meeko-ccd-retry", "components": attempted,
              "template_candidates": len(found),
              "warning": "A template retry does not establish correct charge or docking validity."}
    (directory / "cofactor_resolution.json").write_text(json.dumps(report, indent=2))
    return found, report
