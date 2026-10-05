from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


WORKSPACES = (
    ("Structure", "1 · Load complex", "Load or inspect the experimental complex"),
    ("Preparation", "2 · Prepare structures", "Prepare the receptor and crystallographic reference"),
    ("Validation", "3 · Validate docking", "Test pose recovery by redocking"),
    ("Docking", "4 · Run experiment", "Prepare candidates and run docking"),
    ("Results", "5 · Explore results", "Filter, inspect and export calculations"),
)

_STAGE_BY_VIEW = {view: stage for view, stage, _ in WORKSPACES}
_VIEW_BY_STAGE = {stage: view for view, stage, _ in WORKSPACES}
_DESCRIPTION_BY_VIEW = {view: description for view, _, description in WORKSPACES}


def stage_for_view(view: str) -> str:
    return _STAGE_BY_VIEW.get(view, WORKSPACES[0][1])


def view_from_stage(stage: str | None) -> str:
    if not stage:
        return WORKSPACES[0][0]
    if stage in _VIEW_BY_STAGE:
        return _VIEW_BY_STAGE[stage]
    for view, legacy, _ in WORKSPACES:
        if str(stage).startswith(legacy.split(" · ", 1)[0]):
            return view
    return WORKSPACES[0][0]


def _job_facts(root: Path, jobs_module) -> dict:
    try:
        job_paths = list(jobs_module.list_jobs(root))
    except Exception:
        job_paths = []

    completed = 0
    running = 0
    validation_completed = 0
    experiment_completed = 0
    for path in job_paths:
        try:
            state = jobs_module.status(path).get("state")
        except Exception:
            state = "unknown"
        if state == "completed":
            completed += 1
        if state in {"queued", "running", "starting"}:
            running += 1
        try:
            config = json.loads((Path(path) / "config.json").read_text())
        except Exception:
            config = {}
        if state == "completed":
            if config.get("reference"):
                validation_completed += 1
            else:
                experiment_completed += 1

    return {
        "jobs_total": len(job_paths),
        "jobs_completed": completed,
        "jobs_running": running,
        "validation_completed": validation_completed,
        "experiment_completed": experiment_completed,
    }


def project_snapshot(session_state, root: Path, jobs_module) -> dict:
    candidates = session_state.get("candidate_paths") or []
    snapshot = {
        "complex_loaded": bool(session_state.get("pdb")),
        "receptor_prepared": bool(session_state.get("receptor_path") or session_state.get("preparation_id")),
        "reference_prepared": bool(session_state.get("reference_path")),
        "candidate_count": len(candidates),
    }
    snapshot.update(_job_facts(root, jobs_module))
    return snapshot


def recommended_view(snapshot: dict) -> str:
    if not snapshot["complex_loaded"]:
        return "Structure"
    if not snapshot["receptor_prepared"] or not snapshot["reference_prepared"]:
        return "Preparation"
    if snapshot["validation_completed"] == 0:
        return "Validation"
    if snapshot["experiment_completed"] == 0:
        return "Docking"
    return "Results"


def _status_text(view: str, snapshot: dict) -> str:
    if view == "Structure":
        return "loaded" if snapshot["complex_loaded"] else "empty"
    if view == "Preparation":
        ready = int(snapshot["receptor_prepared"]) + int(snapshot["reference_prepared"])
        return f"{ready}/2 ready"
    if view == "Validation":
        return f"{snapshot['validation_completed']} completed"
    if view == "Docking":
        return f"{snapshot['candidate_count']} candidates"
    return f"{snapshot['jobs_total']} calculations"


def render_sidebar(st, session_state, root: Path, jobs_module):
    snapshot = project_snapshot(session_state, root, jobs_module)

    legacy_stage = session_state.get("workflow_stage")
    current = session_state.get("workspace_view")
    if legacy_stage and (not current or stage_for_view(current) != legacy_stage):
        current = view_from_stage(legacy_stage)
    if current not in _STAGE_BY_VIEW:
        current = recommended_view(snapshot)

    session_state.workspace_view = current
    session_state.workflow_stage = stage_for_view(current)

    st.title("PanDoc")
    st.caption("Interactive docking workspace")
    st.text_input("Experiment name", "My docking experiment", key="experiment")

    st.markdown("**Project workspace**")
    for view, _, _ in WORKSPACES:
        marker = "●" if view == current else "○"
        label = f"{marker} {view} · {_status_text(view, snapshot)}"
        if st.button(
            label,
            key=f"workspace_nav_{view}",
            use_container_width=True,
            type="primary" if view == current else "secondary",
        ):
            session_state.workspace_view = view
            session_state.workflow_stage = stage_for_view(view)
            st.rerun()

    suggested = recommended_view(snapshot)
    if suggested != current:
        st.caption("Suggested next action")
        if st.button(
            f"Continue with {suggested}",
            key="workspace_recommended",
            use_container_width=True,
        ):
            session_state.workspace_view = suggested
            session_state.workflow_stage = stage_for_view(suggested)
            st.rerun()

    st.divider()
    object_bits = [
        "complex" if snapshot["complex_loaded"] else "no complex",
        "receptor" if snapshot["receptor_prepared"] else "no receptor",
        "reference" if snapshot["reference_prepared"] else "no reference",
        f"{snapshot['candidate_count']} candidates",
    ]
    st.caption("Project objects · " + " · ".join(object_bits))
    if snapshot["jobs_running"]:
        st.caption(f"{snapshot['jobs_running']} calculation(s) currently active")
    elif snapshot["jobs_total"]:
        st.caption(f"{snapshot['jobs_completed']}/{snapshot['jobs_total']} calculations completed")
    st.caption("Coordinates in Å · Vina scores in kcal/mol")

    if st.button("Start a new experiment", use_container_width=True):
        session_state.clear()
        st.rerun()

    return stage_for_view(current), snapshot


def render_workspace_header(st, session_state, stage: str, snapshot: dict):
    view = view_from_stage(stage)
    st.title(view)
    st.caption(_DESCRIPTION_BY_VIEW[view])

    actions = []
    if snapshot["complex_loaded"] and view != "Structure":
        actions.append(("Inspect structure", "Structure"))
    if snapshot["complex_loaded"] and view != "Preparation":
        actions.append(("Prepare / edit", "Preparation"))
    if snapshot["receptor_prepared"] and snapshot["reference_prepared"] and view != "Validation":
        actions.append(("Validate protocol", "Validation"))
    if snapshot["receptor_prepared"] and view != "Docking":
        actions.append(("Dock candidates", "Docking"))
    if snapshot["jobs_total"] and view != "Results":
        actions.append(("Explore results", "Results"))

    if actions:
        st.caption("Available from this project state")
        cols = st.columns(min(len(actions), 4))
        for i, (label, target) in enumerate(actions[:4]):
            if cols[i].button(label, key=f"context_action_{view}_{target}", use_container_width=True):
                session_state.workspace_view = target
                session_state.workflow_stage = stage_for_view(target)
                st.rerun()


def select_candidates(st, candidates: list[dict], key: str = "candidate_run_selection") -> list[dict]:
    if not candidates:
        return []

    by_id = {str(item["id"]): item for item in candidates}
    ids = list(by_id)
    previous = st.session_state.get(key)
    default = [item for item in (previous or ids) if item in by_id]
    if not default:
        default = ids

    selected_ids = st.multiselect(
        "Ligands included in this docking run",
        ids,
        default=default,
        format_func=lambda item_id: by_id[item_id].get("name", item_id),
        key=key,
        help="Prepared ligands remain in the project. Select only the subset you want in this run.",
    )
    selected = [by_id[item_id] for item_id in selected_ids]

    table = pd.DataFrame(candidates).drop(columns=["path"], errors="ignore")
    table.insert(0, "Included", table["id"].astype(str).isin(set(selected_ids)))
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(f"{len(selected)} of {len(candidates)} prepared ligand(s) selected for this run.")
    return selected


def filter_results(st, df: pd.DataFrame, key: str = "results_explorer") -> pd.DataFrame:
    if df.empty:
        return df

    filtered = df
    with st.expander("Explore this result set", expanded=True):
        left, middle, right = st.columns(3)

        if "ligand" in df.columns:
            ligand_values = list(dict.fromkeys(df["ligand"].astype(str).tolist()))
            selected_ligands = left.multiselect(
                "Ligands",
                ligand_values,
                default=ligand_values,
                key=f"{key}_ligands",
            )
            filtered = filtered[filtered["ligand"].astype(str).isin(selected_ligands)]

        if "seed" in df.columns:
            seed_values = sorted(df["seed"].dropna().unique().tolist())
            selected_seeds = middle.multiselect(
                "Seeds",
                seed_values,
                default=seed_values,
                key=f"{key}_seeds",
            )
            filtered = filtered[filtered["seed"].isin(selected_seeds)]

        if "rank" in df.columns and not df["rank"].dropna().empty:
            max_rank = int(df["rank"].max())
            rank_limit = right.slider(
                "Maximum pose rank",
                min_value=1,
                max_value=max_rank,
                value=max_rank,
                key=f"{key}_rank",
            )
            filtered = filtered[filtered["rank"] <= rank_limit]

        sortable = [
            col
            for col in ("score_kcal_mol", "reference_rmsd_A", "rank", "seed", "ligand")
            if col in filtered.columns
        ]
        if sortable:
            sort_col = st.selectbox(
                "Sort visible results by",
                sortable,
                key=f"{key}_sort",
            )
            ascending = sort_col != "ligand"
            filtered = filtered.sort_values(sort_col, ascending=ascending, kind="stable")

    st.caption(f"Showing {len(filtered)} of {len(df)} result rows. Filters affect the table, pose list and CSV export only.")
    return filtered
