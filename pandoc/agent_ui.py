"""Streamlit pilot panel for the durable scientific task agent.

The panel is opt-in, access-key gated and single-tenant. Multi-user public
operation requires identity-scoped artifact storage and per-user authorization.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import secrets

from . import task_agent, agent_planner


def _secret(st, key):
    try:
        return str(st.secrets.get(key, os.environ.get(key, "")) or "").strip()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return str(os.environ.get(key, "")).strip()



def _task_root(st):
    """Use stable host storage, not the random per-WebSocket project directory."""
    return Path(_secret(st, "PANDOC_AGENT_DATA_DIR") or
                (Path(os.environ.get("PANDOC_DATA_DIR", tempfile.gettempdir()))
                 / "pandoc_agent"))


def handle_chat_request(st, question):
    """Start only an explicitly requested, PDB-specific read-only task from chat.

    User-facing approvals and all chemistry/compute steps stay in the agent
    panel. Do not interpret explanations ('How do I prepare...?') as commands.
    Return None for ordinary messages, preserving existing Groq conversation.
    """
    import re

    if not re.match(r"^\s*(?:please\s+)?(?:prepare|inspect|retrieve|load|"
                    r"start|validate|redock)\b", str(question), re.I):
        return None
    parsed = task_agent.parse_request(question)
    if parsed["pdb_id"] is None:
        return None
    if _secret(st, "PANDOC_AGENT_ENABLED").lower() not in ("1", "yes", "true"):
        return "Scientific task execution is not enabled in this PanDoc deployment."
    access_key = _secret(st, "PANDOC_AGENT_ACCESS_KEY")
    entered = st.session_state.get("pandoc_agent_access_entry", "")
    if not access_key or not secrets.compare_digest(entered, access_key):
        return ("Open **AI task agent · Perform reviewed scientific work** below "
                "the assistant and enter your agent access key first.")

    plan = agent_planner.plan_request(
        question, api_key=_secret(st, "GROQ_API_KEY"),
        model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL
    )
    result = task_agent.create(_task_root(st), question, plan=plan)
    st.session_state["pandoc_agent_task_id"] = result["id"]
    if result["stage"] == "await_structure_review":
        return (f"Created task **{result['id']}** for PDB **{result['pdb_id']}**. "
                "The structure was retrieved and inspected. Open the **AI task agent** "
                "panel to review its receptor chains and crystal reference before "
                "any preparation or redocking. No calculation has been submitted.")
    return (f"Created task **{result['id']}**. Open the AI task agent panel to "
            "provide an exact PDB identifier before work can continue.")


def render(st, root, backend_factory):
    if _secret(st, "PANDOC_AGENT_ENABLED").lower() not in ("1", "yes", "true"):
        return
    # Use stable host storage rather than the random per-WebSocket app root.
    # Set PANDOC_AGENT_DATA_DIR to mounted durable storage in production.
    root = _task_root(st)
    with st.expander("AI task agent · Perform reviewed scientific work", expanded=False):
        st.caption(
            "Pilot mode: PDB retrieval, structure review, pH proposals, approved "
            "receptor/reference preparation and approved redocking. The agent "
            "never silently accepts chemical states or launches compute."
        )
        access_key = _secret(st, "PANDOC_AGENT_ACCESS_KEY")
        if not access_key:
            st.error("Agent access is not configured. Set PANDOC_AGENT_ACCESS_KEY in app secrets.")
            return
        typed = st.text_input("Agent access key", type="password", key="pandoc_agent_access_entry")
        if not secrets.compare_digest(typed, access_key):
            st.info("Enter the configured agent access key to use the scientific task agent.")
            return
        st.text_area(
            "What should PanDoc do?",
            placeholder="Prepare PDB 1LF2 at pH 5.0 and validate by redocking",
            max_chars=2000, height=90, key="pandoc_agent_instruction"
        )
        if st.button("Create and inspect task", key="pandoc_agent_create_task"):
            try:
                instruction = st.session_state.pandoc_agent_instruction
                with st.spinner("Planning and inspecting the structure..."):
                    plan = agent_planner.plan_request(
                        instruction,
                        api_key=_secret(st, "GROQ_API_KEY"),
                        model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL,
                    )
                    created = task_agent.create(root, instruction, plan=plan)
                st.session_state.pandoc_agent_task_id = created["id"]
                st.rerun()
            except (task_agent.AgentError, ValueError, OSError) as exc:
                st.error(str(exc))

        current_id = st.text_input(
            "Task ID (paste to resume after reload)",
            value=st.session_state.get("pandoc_agent_task_id", ""),
            key="pandoc_agent_resume_id"
        ).strip()
        if not current_id:
            return
        try:
            view = task_agent.describe(root, current_id)
        except task_agent.AgentError as exc:
            st.warning(str(exc))
            return
        stage = view["stage"]
        st.write(f"**Task:** {current_id} · **Stage:** {stage}")
        if view.get("plan"):
            st.caption(view["plan"]["summary"])
            st.caption("Scientific caution: " + view["plan"]["scientific_caution"])
        st.caption(f"PDB: {view.get('pdb_id') or 'Not provided'} · Target pH: {view.get('pH')}")
        if stage == "needs_pdb_id":
            st.warning("Create a new task with an explicit PDB ID; structure identity is never guessed.")
            return

        if stage == "await_structure_review":
            st.write("**Structure inspection**")
            st.write("Receptor chains:", ", ".join(view["receptor_chains"]))
            st.write("Reported structural issues:", view["structural_issue_count"])
            if view["structural_issues"]:
                with st.expander("Inspect coordinate warnings"):
                    st.dataframe(view["structural_issues"], hide_index=True)
            options = view["reference_candidates"]
            if not options:
                st.error("No crystallographic reference ligand identified. Redocking cannot continue automatically.")
                return
            folder = Path(root) / "agent_tasks" / current_id
            inventory = json.loads((folder / "inspection.json").read_text())
            candidates = [x for x in inventory["components"]
                          if x["kind"] in ("Water", "Other component", "Metal / ion")]
            with st.form("pandoc_agent_structure_review"):
                chains = st.multiselect("Receptor protein chains",
                                        view["receptor_chains"],
                                        default=view["receptor_chains"])
                reference = st.selectbox(
                    "Crystallographic reference ligand",
                    [x["residue"] for x in options],
                    format_func=lambda ident: next(
                        f"{x['component']} · {ident} · {x['heavy_atoms']} heavy atoms"
                        for x in options if x["residue"] == ident
                    )
                )
                retained = st.multiselect(
                    "Retain reviewed waters, ions or cofactors (optional)",
                    [x["residue"] for x in candidates if x["residue"] != reference],
                    help="No non-protein components are automatically retained; explicitly select any required components."
                )
                approved = st.checkbox(
                    "I reviewed the protein chains, crystal ligand and retained components."
                )
                accept = st.form_submit_button("Approve structure selection", disabled=not approved)
            if accept:
                try:
                    task_agent.select_structure(root, current_id, chains, reference, retain=retained)
                    st.rerun()
                except (task_agent.AgentError, ValueError) as exc:
                    st.error(str(exc))
            return

        if stage == "await_chemistry_review":
            st.write("**Chemical-state review**")
            st.info("Review the crystallographic ligand identity and any PROPKA proposals. No protonation state is automatically accepted.")
            if st.button("Calculate protein pKa proposals with PROPKA", key="agent_propka"):
                try:
                    with st.spinner("Calculating pKa proposals..."):
                        task_agent.protonation(root, current_id)
                    st.rerun()
                except (task_agent.AgentError, ValueError, OSError) as exc:
                    st.error(str(exc))
            folder = Path(root) / "agent_tasks" / current_id
            proposal_path = folder / "protonation.json"
            if proposal_path.is_file():
                proposal = json.loads(proposal_path.read_text())
                st.caption(f"Predicted titratable residues: {len(proposal['residues'])}; requiring review: {len(proposal['review_required'])}")
                with st.expander("Inspect pKa and proposed residue states"):
                    st.dataframe(proposal["residues"], hide_index=True)
                    st.code(proposal["proposed_assignments"])
            if st.button("Retrieve CCD chemistry and ligand microstates", key="agent_ligand_options"):
                try:
                    with st.spinner("Retrieving CCD chemistry and enumerating candidate states..."):
                        task_agent.ligand_options(root, current_id)
                    st.rerun()
                except (task_agent.AgentError, ValueError, OSError) as exc:
                    st.error(str(exc))
            smiles_options = []
            ligand_path = folder / "ligand_options.json"
            if ligand_path.is_file():
                ligand_data = json.loads(ligand_path.read_text())
                comparison = ligand_data["heavy_atom_comparison"]
                if not comparison.get("valid"):
                    st.warning("CCD and crystal ligand composition differ: " + comparison.get("message", "Review identity."))
                else:
                    smiles_options.append(ligand_data["ccd_smiles"])
                    smiles_options.extend(x["smiles"] for x in ligand_data["microstates"])
                if ligand_data["microstates"]:
                    with st.expander("Review candidate ligand microstates"):
                        st.dataframe(ligand_data["microstates"], hide_index=True)
                st.caption(ligand_data["warning"])
            choices = list(dict.fromkeys(smiles_options))
            proposed_smiles = st.selectbox(
                "CCD/MolScrub candidate (review before using)",
                ["Enter or edit SMILES manually"] + choices,
                key="agent_reference_candidate",
            ) if choices else ""
            with st.form("pandoc_agent_chemistry"):
                smiles = st.text_input("Reviewed crystallographic reference ligand isomeric SMILES",
                                       value=proposed_smiles if proposed_smiles in choices else "")
                templates = st.text_area(
                    "Reviewed Meeko residue template assignments (optional)",
                    placeholder="A:17=HID,A:32=ASH"
                )
                repair = st.checkbox("Rebuild missing receptor heavy atoms with PDBFixer", value=True)
                approved = st.checkbox(
                    "I reviewed ligand chemistry and residue protonation decisions."
                )
                run = st.form_submit_button("Prepare reviewed receptor and reference",
                                            disabled=not approved or not smiles)
            if run:
                try:
                    with st.spinner("Preparing and auditing receptor/reference structures..."):
                        task_agent.prepare(
                            root, current_id,
                            reference_smiles=smiles,
                            template_assignments=templates,
                            repair_missing_heavy_atoms=repair,
                            approved=approved,
                        )
                    st.rerun()
                except (task_agent.AgentError, ValueError, RuntimeError, OSError) as exc:
                    st.error(str(exc))
            return

        if stage == "await_docking_approval":
            defaults = view["docking_proposal"]
            st.write("**Redocking proposal**")
            with st.form("pandoc_agent_redocking"):
                center = st.text_input("Docking center [x,y,z] (Å)", json.dumps(defaults["center_A"]))
                size = st.text_input("Docking box size [x,y,z] (Å)", json.dumps(defaults["size_A"]))
                exhaustiveness = st.number_input("Exhaustiveness", 1, 64, defaults["exhaustiveness"])
                poses = st.number_input("Maximum poses", 1, 20, defaults["poses"])
                seeds = st.text_input("Independent seeds (comma-separated)",
                                      ",".join(map(str, defaults["seeds"])))
                cpu = st.number_input("CPU threads", 1, 8, defaults["cpu"])
                approved = st.checkbox(
                    "I approve this receptor, crystal reference, grid, compute settings and redocking run."
                )
                submit = st.form_submit_button("Submit approved redocking",
                                                disabled=not approved)
            if submit:
                try:
                    backend = backend_factory()
                    if backend is None:
                        st.error("No compute backend is configured.")
                        return
                    task_agent.submit_redocking(
                        root, current_id, approved=approved, backend=backend,
                        center=json.loads(center), size=json.loads(size),
                        exhaustiveness=int(exhaustiveness), poses=int(poses),
                        seeds=[int(x.strip()) for x in seeds.split(",") if x.strip()],
                        cpu=int(cpu),
                    )
                    st.rerun()
                except (task_agent.AgentError, ValueError, RuntimeError, OSError,
                        json.JSONDecodeError) as exc:
                    st.error(str(exc))
            return

        if stage == "running":
            st.write("**Redocking job:**", view.get("job_id"))
            st.caption("Your task record is persistent. Refresh reads the actual remote job status.")
            if st.button("Refresh job and collect results", key="pandoc_agent_refresh"):
                try:
                    task_agent.refresh(root, current_id, backend_factory())
                    st.rerun()
                except (task_agent.AgentError, RuntimeError, OSError) as exc:
                    st.error(str(exc))
            if st.button("Request cancellation", key="pandoc_agent_cancel"):
                try:
                    task_agent.cancel(root, current_id, backend_factory())
                    st.rerun()
                except (task_agent.AgentError, RuntimeError, OSError) as exc:
                    st.error(str(exc))
            if view.get("remote_state"):
                st.json(view["remote_state"])
            return

        if stage == "completed":
            st.success("Redocking completed and results were collected.")
            summary = view.get("summary", {})
            st.metric("Saved poses", summary.get("saved_pose_count", 0))
            if summary.get("compounds"):
                st.dataframe(summary["compounds"], hide_index=True)
            st.caption("Pose recovery is not proof of experimental binding affinity.")
            st.download_button(
                "Download reviewed task bundle",
                data=task_agent.artifacts(root, current_id),
                file_name=f"pandoc_agent_{current_id}.zip",
                mime="application/zip",
            )
            return

        if stage == "submitting" or stage == "submission_uncertain":
            st.warning("Submission status is uncertain. Do not create a duplicate docking job; reconcile the backend first.")
        elif stage in ("preparation_failed", "failed"):
            st.error(view.get("failure", "Task failed. Review the scientific diagnostics."))
        elif stage == "cancelled":
            st.info("Task was cancelled.")
