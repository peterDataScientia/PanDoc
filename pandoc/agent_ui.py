"""Streamlit pilot panel for the durable scientific task agent.

The panel is opt-in, access-key gated and single-tenant. Multi-user public
operation requires identity-scoped artifact storage and per-user authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
import secrets

from . import task_agent, agent_planner, agent_conversation


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


def _agent_authorized(st):
    """Require a successful explicit unlock for this Streamlit browser session.

    The verifier is invalidated when the configured server secret changes.
    The submitted plaintext key is never forwarded to Groq or scientific tools.
    """
    configured = _secret(st, "PANDOC_AGENT_ACCESS_KEY")
    if not configured:
        return False
    fingerprint = hashlib.sha256(configured.encode("utf-8")).hexdigest()
    return bool(
        st.session_state.get("pandoc_agent_unlocked")
        and secrets.compare_digest(
            str(st.session_state.get("pandoc_agent_key_verifier", "")), fingerprint
        )
    )


def handle_chat_request(st, question, backend_factory=None):
    """Route task follow-ups from the existing scientific assistant chat.

    Ordinary greetings and product explanations still use the generic Groq
    assistant. A verified active task receives contextual questions; nothing
    auto-approves the scientist's chemistry or launches remote jobs.
    """
    import re

    message = str(question).strip()
    enabled = _secret(st, "PANDOC_AGENT_ENABLED").lower() in ("1", "yes", "true")
    allowed = bool(enabled and _agent_authorized(st))
    # Do not let a task monopolize greetings or general software questions.
    is_generic = (
        bool(re.fullmatch(r"(hi|hello|hey|habari|mambo|thanks|thank you)[!. ]*",
                          message.casefold())) or
        bool(re.match(r"^\s*(what is|tell me about|describe)\s+(pandoc|this software|the software)\b",
                      message, re.I))
    )
    active = str(st.session_state.get("pandoc_agent_task_id") or "")
    if allowed and re.fullmatch(r"[0-9a-f]{32}", active) and not is_generic:
        try:
            current = task_agent.describe(_task_root(st), active)
        except task_agent.AgentError:
            current = None
        if current is not None:
            new_request = bool(re.match(r"^\s*(?:start|create)\s+(?:a\s+)?new\s+task\b", message, re.I))
            if not new_request:
                # A request to discuss a different PDB must not silently act
                # on the previously inspected structure.
                parsed = task_agent.parse_request(message)
                if parsed["pdb_id"] and parsed["pdb_id"] != current["pdb_id"]:
                    return ("That request names a different PDB. Start a separate task "
                            "under 'What should PanDoc do?' to avoid mixing structures.")
                return agent_conversation.respond(
                    _task_root(st), active, message,
                    api_key=_secret(st, "GROQ_API_KEY"),
                    model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL,
                    backend_factory=backend_factory,
                )

    # First-time explicit action prompts start a task. An ordinary question
    # like "How do I prepare..." must never create one.
    if not re.match(r"^\s*(?:(?:please)\s+)?(?:prepare|inspect|retrieve|load|"
                    r"start|validate|redock)\b", message, re.I):
        return None
    parsed = task_agent.parse_request(message)
    if parsed["pdb_id"] is None:
        return None
    if not enabled:
        return "Scientific task execution is not enabled in this PanDoc deployment."
    if not allowed:
        return ("Open **AI task agent · Perform reviewed scientific work** below "
                "the assistant and enter your agent access key first.")

    plan = agent_planner.plan_request(
        message, api_key=_secret(st, "GROQ_API_KEY"),
        model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL
    )
    result = task_agent.create(_task_root(st), message, plan=plan)
    st.session_state["pandoc_agent_task_id"] = result["id"]
    if result["stage"] == "await_structure_review":
        return (f"Created task **{result['id']}** for PDB **{result['pdb_id']}**. "
                "The structure was retrieved and inspected. Open the **AI task agent** "
                "panel to review its findings and keep discussing the same task. "
                "No calculation has been submitted.")
    return (f"Created task **{result['id']}**. Open the AI task agent panel to "
            "provide an exact PDB identifier before work can continue.")


def render_task_chat(st, root, task_id, backend_factory):
    """Always-visible follow-up conversation for the active scientific task."""
    st.markdown("**Continue with this task · Scientific agent conversation**")
    st.caption(
        "Ask follow-up questions, review findings or request the next safe action. "
        "The conversation stays attached to this task across app reruns. "
        "Chemical decisions and docking still require explicit approval."
    )
    transcript = agent_conversation.history(root, task_id)
    with st.container(height=270, border=True):
        if not transcript:
            # In regular deployments, show a grounded first finding, not
            # an empty chat box. AppTest's fake task may omit task.json.
            if (Path(root) / "agent_tasks" / task_id / "task.json").is_file():
                with st.chat_message("assistant"):
                    st.markdown(agent_conversation.opening(root, task_id))
            else:
                st.caption("Ask about the current task to begin its conversation.")
        for turn in transcript[-10:]:
            with st.chat_message("user"):
                st.markdown(turn["question"])
            with st.chat_message("assistant"):
                st.markdown(turn["answer"])
    example_buttons = st.columns(3)
    suggestions = (
        ("What did you find?", "What did you find in this structure?"),
        ("Which ligands?", "Which crystallographic ligand candidates were detected?"),
        ("What next?", "What should we do next in this task?"),
    )
    pending = None
    for i, (col, (label, prompt)) in enumerate(zip(example_buttons, suggestions)):
        if col.button(label, key=f"agent_quick_{i}", width="stretch"):
            pending = prompt
    with st.form("pandoc_agent_followup_form", clear_on_submit=True):
        followup = st.text_input(
            "Message your task agent",
            placeholder="What did you find? Which ligand is suitable? Prepare it at pH 5.0.",
            key="pandoc_agent_followup", max_chars=2000,
        )
        submitted = st.form_submit_button("Send follow-up to agent", type="primary")
    if pending is None and not submitted:
        return
    question = pending if pending is not None else followup.strip()
    if not question:
        st.warning("Enter a follow-up question.")
        return
    try:
        with st.spinner("Reviewing task evidence..."):
            agent_conversation.respond(
                root, task_id, question,
                api_key=_secret(st, "GROQ_API_KEY"),
                model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL,
                backend_factory=backend_factory,
            )
        st.rerun()
    except (task_agent.AgentError, OSError, RuntimeError, ValueError) as exc:
        st.error(str(exc))


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
        if not _agent_authorized(st):
            st.caption(
                "Enter your **PANDOC_AGENT_ACCESS_KEY** (not the Groq API key), "
                "then press **Unlock agent**. The password field alone does not start a task."
            )
            with st.form("pandoc_agent_unlock_form", clear_on_submit=True):
                typed = st.text_input(
                    "Agent access key",
                    type="password",
                    key="pandoc_agent_access_entry",
                    placeholder="Paste the agent access key",
                )
                submitted_key = st.form_submit_button(
                    "Unlock agent", type="primary"
                )
            if submitted_key:
                if typed and secrets.compare_digest(typed.strip(), access_key):
                    st.session_state["pandoc_agent_unlocked"] = True
                    st.session_state["pandoc_agent_key_verifier"] = hashlib.sha256(
                        access_key.encode("utf-8")
                    ).hexdigest()
                    st.rerun()
                else:
                    st.error(
                        "Access key not recognized. Copy the exact value of "
                        "PANDOC_AGENT_ACCESS_KEY from Streamlit app secrets, "
                        "not GROQ_API_KEY or PANDOC_JOB_KEY."
                    )
            else:
                st.info("Agent locked · Enter the key and click Unlock agent.")
            return

        st.success("Agent unlocked · Ready to inspect structures and continue tasks.")
        if st.button("Lock agent", key="pandoc_agent_lock_button"):
            st.session_state.pop("pandoc_agent_unlocked", None)
            st.session_state.pop("pandoc_agent_key_verifier", None)
            st.rerun()
        st.caption(
            "Describe the work below and press **Start task**. PanDoc creates "
            "a task ID automatically; do not type the instruction in the resume field."
        )
        with st.form("pandoc_agent_start_form", clear_on_submit=False):
            instruction = st.text_area(
                "What should PanDoc do?",
                placeholder="Inspect PDB 1LF2. Or prepare PDB 1LF2 at pH 5.0 and validate by redocking.",
                max_chars=2000, height=90, key="pandoc_agent_instruction",
            )
            start_task = st.form_submit_button(
                "Start task · Retrieve and inspect PDB", type="primary",
            )
        if start_task:
            try:
                # A task request is NOT a task ID. It is parsed and the ID is
                # created by task_agent.create after successful submission.
                task_agent.parse_request(instruction)
                with st.spinner("Planning and inspecting the structure..."):
                    plan = agent_planner.plan_request(
                        instruction,
                        api_key=_secret(st, "GROQ_API_KEY"),
                        model=_secret(st, "GROQ_MODEL") or agent_planner.MODEL,
                    )
                    created = task_agent.create(root, instruction, plan=plan)
                st.session_state["pandoc_agent_task_id"] = created["id"]
                st.session_state.pop("pandoc_agent_resume_candidate", None)
                st.rerun()
            except (task_agent.AgentError, ValueError, RuntimeError, OSError) as exc:
                st.error(str(exc))

        # Resume is deliberately separate from instructions. Never attempt
        # to look up what the user typed into the task-request text area.
        with st.expander("Resume a previous task (optional)", expanded=False):
            st.caption(
                "Only use this after you already have a 32-character task ID "
                "from an earlier task. For new requests, use Start task above."
            )
            resume = st.text_input(
                "Previously generated task ID",
                placeholder="Paste the 32-character ID, not an instruction",
                key="pandoc_agent_resume_candidate",
            )
            if st.button("Resume existing task", key="pandoc_agent_resume_button"):
                candidate = (resume or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{32}", candidate):
                    st.warning(
                        "That is not a task ID. To inspect PDB 1LF2, enter "
                        "'Inspect PDB 1LF2' under 'What should PanDoc do?' "
                        "and click 'Start task'."
                    )
                else:
                    try:
                        task_agent.describe(root, candidate)
                        st.session_state["pandoc_agent_task_id"] = candidate
                        st.rerun()
                    except task_agent.AgentError:
                        st.warning(
                            "That task ID was not found on this server. "
                            "Tasks on temporary hosting storage may be lost after a restart."
                        )

        current_id = str(st.session_state.get("pandoc_agent_task_id") or "").strip()
        if not current_id:
            st.info("No task started yet. Enter a request above and click Start task.")
            return
        if not re.fullmatch(r"[0-9a-f]{32}", current_id):
            st.session_state.pop("pandoc_agent_task_id", None)
            st.warning("Previous task reference was invalid. Start a task above.")
            return
        try:
            view = task_agent.describe(root, current_id)
        except task_agent.AgentError:
            st.warning(
                "Your previously selected task cannot be found on this server. "
                "Its storage may have been cleared. Start a new task above, "
                "or resume another saved task by its generated ID."
            )
            return
        stage = view["stage"]
        st.write(f"**Task stage:** {stage}")
        st.caption("Generated task ID (save this for resuming later):")
        st.code(current_id, language=None)
        if view.get("plan"):
            st.caption(view["plan"]["summary"])
            st.caption("Scientific caution: " + view["plan"]["scientific_caution"])
        st.caption(f"PDB: {view.get('pdb_id') or 'Not provided'} · Target pH: {view.get('pH')}")
        render_task_chat(st, root, current_id, backend_factory)
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
            # Inspection-only requests end at a useful structural report.
            # They must not force the user into reference-ligand selection.
            if view["goal"] == "inspection":
                folder = Path(root) / "agent_tasks" / current_id
                inventory = json.loads((folder / "inspection.json").read_text())
                st.dataframe(inventory["components"], hide_index=True)
                if inventory.get("alternates"):
                    with st.expander("Alternate atom positions"):
                        st.dataframe(inventory["alternates"], hide_index=True)
                st.success(
                    "Structure inspection is complete. No preparation or "
                    "docking calculations have been started."
                )
                return
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
