import json

from pandoc import profile_quality


def receptor(tmp_path):
    path = tmp_path / "receptor.pdbqt"
    prefix = (
        "ATOM      1  CA  ALA A   1    "
        "   1.000   2.000   3.000  1.00 20.00"
    )
    path.write_text(prefix.ljust(70) + f"{-0.25:6.3f} {'C':>2}\n")
    return path


def assess(path, **kwargs):
    arguments = dict(
        target="AR",
        preparation_mode="standard",
        receptor_pdbqt=path,
    )
    arguments.update(kwargs)
    return profile_quality.assess(**arguments)


def test_clean_prepared_receptor_is_ready_for_redocking_not_publication(tmp_path):
    result = assess(receptor(tmp_path))
    assert result["readiness"] == "ready_for_redocking"
    assert result["ready_for_redocking"] is True
    assert result["production_validated"] is False


def test_remote_chain_break_is_warning_not_blocker(tmp_path):
    result = assess(
        receptor(tmp_path),
        structure_issues=[
            {
                "severity": "Warning",
                "residue": "A:850:THR",
                "problem": "Possible chain break after A:843:ALA (12.20 Å)",
                "action": "Document the gap.",
            }
        ],
    )
    assert result["readiness"] == "ready_with_warnings"
    assert result["warning_count"] == 1
    assert result["ready_for_redocking"] is True


def test_distant_audited_meeko_exclusion_is_warning_not_perfection_gate(tmp_path):
    result = assess(
        receptor(tmp_path),
        remote_exclusions=[
            {
                "residue": "A:932",
                "distance_to_reference_A": 26.326,
                "minimum_allowed_distance_A": 20.0,
            }
        ],
    )
    assert result["readiness"] == "ready_with_warnings"
    assert result["blocking_count"] == 0
    assert result["warning_count"] == 1


def test_near_pocket_or_unmeasured_exclusion_is_blocked(tmp_path):
    path = receptor(tmp_path)
    for distance in (2.5, None):
        result = assess(
            path,
            remote_exclusions=[
                {
                    "residue": "A:932",
                    "distance_to_reference_A": distance,
                    "minimum_allowed_distance_A": 20.0,
                }
            ],
        )
        assert result["readiness"] == "blocked"
        assert result["blocking_count"] == 1


def test_receptor_integrity_errors_must_still_block(tmp_path):
    result = assess(
        receptor(tmp_path),
        structure_issues=[
            {
                "severity": "Error",
                "residue": "A:12:ALA",
                "problem": "Nonfinite coordinates",
            }
        ],
    )
    assert result["ready_for_redocking"] is False


def test_missing_or_invalid_receptor_is_blocked(tmp_path):
    result = assess(tmp_path / "missing.pdbqt")
    assert result["readiness"] == "blocked"
    path = receptor(tmp_path)
    path.write_text(path.read_text().replace("-0.250", "   nan"))
    result = assess(path)
    assert result["readiness"] == "blocked"


def test_heme_must_have_atom_validation_but_iron_charges_are_review_warning(tmp_path):
    path = receptor(tmp_path)
    lacking = assess(path, preparation_mode="curated_heme", heme_audit=None)
    assert lacking["readiness"] == "blocked"
    reviewed = assess(
        path,
        preparation_mode="curated_heme",
        heme_audit={
            "pdbqt": {
                "heme_atom_count_in_pdbqt": 43,
                "all_receptor_heavy_atom_types_verified": True,
                "iron_parameterization_review_required": True,
            }
        },
    )
    assert reviewed["ready_for_redocking"] is True
    assert reviewed["readiness"] == "ready_with_warnings"
    assert reviewed["production_validated"] is False
    assert any(x["source"] == "iron_scoring" for x in reviewed["warnings"])


def test_manifest_exposes_pending_cyp19a1_in_partial_download(tmp_path):
    requested = ["FYN", "AR", "PGR", "CYP19A1"]
    finished = [
        {"target": "FYN"},
        {"target": "AR"},
        {"target": "PGR"},
    ]
    report = profile_quality.write_manifest(
        tmp_path, requested, finished, [], pending_targets=["CYP19A1"],
    )
    assert report["state"] == "partial"
    assert report["pending_targets"] == ["CYP19A1"]
    assert json.loads((tmp_path / "PROFILE_MANIFEST.json").read_text()) == report

    complete = profile_quality.manifest(
        requested, finished + [{"target": "CYP19A1"}], []
    )
    assert complete["complete"] is True
    assert complete["pending_targets"] == []
