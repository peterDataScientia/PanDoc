"""Structured Groq planning for the deterministic PanDoc task controller.

The model contributes a concise interpretation only. PDB identity, pH, allowed
operations and approval sequence are determined by trusted PanDoc code.
"""
from __future__ import annotations

import json

from . import task_agent

MODEL = "openai/gpt-oss-120b"

STEP_LIBRARY = {
    "inspection": [
        "retrieve_pdb", "inspect_structure", "researcher_select_components",
    ],
    "preparation": [
        "retrieve_pdb", "inspect_structure", "researcher_select_components",
        "predict_optional_pka", "researcher_review_chemistry",
        "prepare_receptor_and_crystal_reference",
    ],
    "redocking": [
        "retrieve_pdb", "inspect_structure", "researcher_select_components",
        "predict_optional_pka", "researcher_review_chemistry",
        "prepare_receptor_and_crystal_reference", "researcher_approve_redocking",
        "submit_redocking", "retrieve_saved_results", "report_pose_recovery",
    ],
}

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "pandoc_task_planning",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "scientific_caution": {"type": "string"},
            },
            "required": ["summary", "scientific_caution"],
            "additionalProperties": False,
        },
    },
}


def plan_request(instruction: str, api_key="", model=MODEL) -> dict:
    """Produce a reviewable plan; no model output can authorize execution."""
    parsed = task_agent.parse_request(instruction)
    goal = parsed["goal"]
    plan = {
        "source": "deterministic",
        "goal": goal,
        "pdb_id": parsed["pdb_id"],
        "pH": parsed["pH"],
        "steps": STEP_LIBRARY[goal],
        "summary": (
            f"Inspect PDB {parsed['pdb_id']} and prepare a researcher-reviewed "
            f"{goal} workflow." if parsed["pdb_id"] else
            "An exact PDB ID is needed before structure retrieval."
        ),
        "scientific_caution": (
            "Researcher approvals are required for component selection and "
            "chemical states; docking execution requires separate approval."
        ),
    }
    if not api_key or not parsed["pdb_id"]:
        return plan

    # Groq is not offered filesystem, compute, API or mutation tools here.
    # Its prose cannot alter the trusted step sequence or accepted identifiers.
    try:
        from groq import Groq
        with Groq(api_key=api_key, timeout=30, max_retries=0) as client:
            result = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content":
                     "You summarize approved scientific workflow intentions for "
                     "PanDoc molecular docking. Make no claims of completed work. "
                     "Do not infer missing ligand identities, residue protonation, "
                     "cofactors, grid coordinates, or scientific validation. "
                     "Write short plain-English summaries, not instructions. "
                     "The next step is read-only RCSB retrieval/inspection. "
                     "Later preparation and docking require explicit approvals."},
                    {"role": "user", "content": (
                        "User task: " + instruction + "\n"
                        "Trusted extracted target: " + json.dumps(parsed)
                    )},
                ],
                response_format=SCHEMA,
                temperature=0.0,
                max_completion_tokens=280,
            )
        value = json.loads(result.choices[0].message.content or "{}")
        if (isinstance(value, dict)
                and isinstance(value.get("summary"), str)
                and isinstance(value.get("scientific_caution"), str)):
            plan["summary"] = value["summary"][:350]
            plan["scientific_caution"] = value["scientific_caution"][:350]
            plan["source"] = "groq_structured"
    except Exception:
        # Loss of Groq quota/network must not fabricate plan state or block
        # explicitly requested read-only PDB inspection.
        plan["source"] = "deterministic_fallback"
    return plan
