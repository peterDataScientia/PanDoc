"""Regression tests for CCD fallback, HEM safety, and audited preparation."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pandoc import cofactor_templates, core


def het(serial, name, res, element, number=1):
    return (
        f"HETATM{serial:5d} {name:>4s} {res:>3s} A{number:4d}    "
        f"{1.:8.3f}{2.:8.3f}{3.:8.3f}{1.:6.2f}{20.:6.2f}          "
        f"{element:>2s}\n"
    )


def test_component_parser_preserves_original_heme_iron():
    pdb = het(1, "FE", "HEM", "FE") + het(2, "C1", "HEM", "C")
    atoms = core.atoms(pdb)
    assert atoms[0]["element"] == "FE"
    groups = cofactor_templates.component_groups(pdb)
    assert list(groups) == ["HEM"]
    assert sum(len(a) for a in groups["HEM"].values()) == 2


def test_heme_requires_explicit_review_and_never_calls_meeko():
    with TemporaryDirectory() as temp, patch("pandoc.core.subprocess.run") as run:
        with pytest.raises(ValueError, match="HEM.*coordinated Fe"):
            core.prepare_receptor(het(1, "FE", "HEM", "FE"), temp)
        run.assert_not_called()
        assert "HEM was not discarded" in (Path(temp) / "preparation.log").read_text()


def test_curated_heme_path_retains_prepared_artifact_and_audit():
    with TemporaryDirectory() as temp:
        destination = Path(temp)
        def curated(*args, **kwargs):
            assert kwargs["coordination_residue"] == "A:25"
            assert "HEM" in kwargs["heme_source_pdb"]
            output = destination / "receptor_curated_heme.pdbqt"
            output.write_text("HETATM    1 FE   HEM A 100       0.000   0.000   0.000  0.00  0.00    FE\n")
            (destination / "protein_reviewed_plus_heme.pdb").write_text(
                het(1, "FE", "HEM", "FE")
            )
            return output, {"status": "prepared_curated_heme"}
        with patch("pandoc.heme_prep.prepare_curated_heme_receptor",
                   side_effect=curated), patch("pandoc.core.subprocess.run") as run:
            path = core.prepare_receptor(
                het(1, "FE", "HEM", "FE"), destination,
                heme_coordination_residue="A:25",
                heme_source_pdb=het(1, "FE", "HEM", "FE"),
            )
        run.assert_not_called()
        assert path.name == "receptor.pdbqt"
        assert path.read_text().startswith("HETATM")
        assert "HEM" in (destination / "receptor_prepared.pdb").read_text()


def test_nonmetal_ccd_failure_retries_once_with_validated_candidate():
    with TemporaryDirectory() as temp:
        destination = Path(temp)
        calls = []
        def simulate(args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return SimpleNamespace(
                    returncode=1, stdout="Failed building template from CCD for resname='EOH'",
                    stderr="")
            (destination / "receptor.pdbqt").write_text("prepared")
            (destination / "receptor_prepared.pdb").write_text(het(1, "C1", "EOH", "C"))
            return SimpleNamespace(returncode=0, stdout="success", stderr="")
        with patch("pandoc.core.subprocess.run", side_effect=simulate), \
             patch("pandoc.cofactor_templates.resolve_ccd_failure",
                   return_value=(
                       ["EOH:/cache/EOH_ideal.sdf"],
                       {"stage": "meeko-ccd-retry", "components": []},
                   )):
            result = core.prepare_receptor(het(1, "C1", "EOH", "C"), destination)
        assert result.is_file()
        assert len(calls) == 2
        assert calls[1][-2:] == ["--add_templates", "EOH:/cache/EOH_ideal.sdf"]
        assert "VALIDATED CCD TEMPLATE RETRY" in (destination / "preparation.log").read_text()
        assert __import__("json").loads(
            (destination / "cofactor_resolution.json").read_text()
        )["retry_successful"] is True


def test_unsafe_metal_sdf_is_not_downloaded():
    with TemporaryDirectory() as temp, patch("requests.get") as get:
        pdb = het(1, "FE", "HEM", "FE")
        with pytest.raises(ValueError, match="metal-containing"):
            cofactor_templates._template_for_component(
                "HEM", cofactor_templates.component_groups(pdb)["HEM"],
                pdb, temp,
            )
        get.assert_not_called()


def test_sdf_validates_heavy_element_inventory():
    rdkit = pytest.importorskip("rdkit")
    from rdkit import Chem
    mol = Chem.MolFromSmiles("CCO")
    sdf = (Chem.MolToMolBlock(mol) + "\n$$$$\n").encode()
    with pytest.raises(ValueError, match="heavy-element inventories differ"):
        cofactor_templates.validate_sdf(
            sdf, {"A:1:EOH": core.atoms(het(1, "C1", "EOH", "C"))}
        )
    proper = het(1, "C1", "EOH", "C") + het(2, "C2", "EOH", "C") + het(3, "O1", "EOH", "O")
    cofactor_templates.validate_sdf(
        sdf, {"A:1:EOH": core.atoms(proper)}
    )


def test_unrelated_valence_failure_does_not_trigger_ccd_fetch():
    assert cofactor_templates.failing_components(
        "Invalid inferred bonding and valence", {"EOH": {}}) == []
    assert cofactor_templates.failing_components(
        "Failed building template from CCD for resname='EOH'", {"EOH": {}}) == ["EOH"]


def test_meeko_success_that_drops_deposited_atom_is_rejected():
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        pdb = het(1, "C1", "EOH", "C") + het(2, "O1", "EOH", "O")

        def simulate(args, **kwargs):
            (directory / "receptor.pdbqt").write_text("prepared")
            (directory / "receptor_prepared.pdb").write_text(het(1, "C1", "EOH", "C"))
            return SimpleNamespace(returncode=0, stdout="success", stderr="")

        with patch("pandoc.core.subprocess.run", side_effect=simulate):
            with pytest.raises(ValueError, match="lost deposited heavy atoms"):
                core.prepare_receptor(pdb, directory)
        assert "O1" in (directory / "preparation.log").read_text()
