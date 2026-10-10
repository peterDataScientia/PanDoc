# PanDoc scientific task agent (pilot v0.1)

This is a functioning, controlled, **single-tenant** task workflow. It is not an unrestricted chatbot. It implements natural-language PDB identification, RCSB retrieval, coordinate inspection, researcher-approved selection, optional PROPKA proposals, reviewed reference/receptor preparation, user-approved Vina redocking, job status recovery, RMSD summarization and reproducible artifact export.

## What the agent can do

| Task step | Independent action | Review required |
| --- | --- | --- |
| Interpret instruction (Groq structured plan or deterministic fallback) | Yes | No identity or pH may be guessed |
| Retrieve PDB entry and inspect atom/component issues | Yes | No |
| Choose protein chains, reference ligand, retained waters/cofactors | No | Researcher selection |
| Run PROPKA at specified pH | On request | Suggested states are advisory |
| Choose reference SMILES, residue templates and atom repair | No | Researcher approval |
| Prepare selected receptor and crystallographic reference | After approval | Receptor/reference chemistry review |
| Choose grid, seeds, search settings and computing resources | No | Separate calculation approval |
| Submit, monitor and collect redocking results | After approval | No silent duplicate submission |
| Build publication/provenance archive | Upon completion | Researcher interprets results |

PanDoc preserves reference-ligand heavy-atom coordinates when preparing the redocking reference; it does not substitute a new RDKit conformer. It blocks generic noncovalent redocking for crystal-reference LINK connections or a retained HEM requiring curated iron coordination. Unexpected failed Meeko/coordinate checks are never silently accepted.

## Streamlit configuration

Set these values in Streamlit App Settings → Secrets (or environment variables):

~~~toml
GROQ_API_KEY = "your-provider-key"                 # optional for structured planning
GROQ_MODEL = "openai/gpt-oss-120b"
PANDOC_AGENT_ENABLED = "true"
PANDOC_AGENT_ACCESS_KEY = "long-private-secret"    # required
PANDOC_AGENT_DATA_DIR = "/mounted/pandoc_tasks"    # required for restart durability
GITHUB_TOKEN = "your-compute-token"
PANDOC_JOB_KEY = "your-Fernet-key"
~~~

Once the agent key is entered, explicit requests such as 'Prepare PDB 1LF2 at pH 5.0' can also be sent in the existing scientific assistant chat; they create a task and pause at the review panel. This does not authorize docking.

Open the existing Scientific assistant panel, then open **AI task agent · Perform reviewed scientific work**. Enter the configured access key and request, for example, "Prepare PDB 1LF2 at pH 5.0 and validate by redocking." The task retrieves a structure and awaits approval of the chains, crystal ligand and retained components. You may inspect PROPKA proposals, retrieve candidate CCD ligand SMILES and enumerate pH-aware MolScrub microstates; no chemical state is automatically accepted.  After explicitly approving chemical preparation, inspect and approve the proposed grid and search settings, then submit redocking. Refresh the remote job to collect results and download the ZIP.

The pilot UI uses the existing GitHub Actions compute backend. Kaggle compute remains in PanDoc but is not wired to this pilot selector. Without valid compute credentials, redocking cannot be submitted.

The task ID can be used to resume as long as the same persistent task root remains accessible. A temporary directory on Streamlit Community Cloud is not durable across host restarts.

## Authenticated REST API

Every route under /api/v1/agent requires a nonempty PANDOC_API_KEY and an X-API-Key request header. Missing server configuration returns HTTP 503. Missing or incorrect client key returns 401. Configure PANDOC_API_DATA_DIR to a durable writable path.

| Method | Route | Description |
| --- | --- | --- |
| POST | /api/v1/agent/tasks | Task request including explicit PDB ID |
| GET | /api/v1/agent/tasks/{id} | Current stage, proposed actions, results |
| POST | /api/v1/agent/tasks/{id}/select | Approve receptor chains, crystal reference and retained components |
| POST | /api/v1/agent/tasks/{id}/protonation | Calculate/read protein pKa suggestions |
| POST | /api/v1/agent/tasks/{id}/ligand-options | Retrieve CCD ligand chemistry and candidate MolScrub microstates for review |
| POST | /api/v1/agent/tasks/{id}/prepare | Explicit approval of reviewed SMILES/templates and receptor preparation |
| POST | /api/v1/agent/tasks/{id}/redock | Approve grid/search settings and launch |
| POST | /api/v1/agent/tasks/{id}/refresh | Poll job and retrieve completed artifacts |
| POST | /api/v1/agent/tasks/{id}/cancel | Request cancellation |
| GET | /api/v1/agent/tasks/{id}/bundle | Authenticated ZIP of inputs, provenance and outputs |

Example create request:

~~~bash
curl -H "X-API-Key: $PANDOC_API_KEY" -H "Content-Type: application/json" \
  -d '{"instruction":"Prepare PDB 1LF2 at pH 5.0 and validate by redocking"}' \
  https://YOUR_PANDOC_API/api/v1/agent/tasks
~~~

## Safety and reliability

Each task has a UUID directory, audit trail, review selections, prepared structure hashes, saved docking settings, remote job handle and outcomes. A submission is committed as 'submitting' before external dispatch. If the result of dispatch is uncertain, the task becomes 'submission_uncertain' and cannot silently resubmit an expensive duplicate job.

Groq produces a bounded, strict-JSON planning summary. Its output cannot change PDB identity, pH, trusted action list or permissions. Scientific code is executed by PanDoc, never by unrestricted model-generated Python or shell commands. Groq quota/network failure falls back to a deterministic plan.

**Pilot limitations:** this workflow uses a process-local lock and a shared access key and is single-tenant. It is not ready for anonymous public multi-user compute. Before broad deployment add owner-specific authentication, isolated private storage, compute limits/budgets, transactional task database and cross-worker idempotency, workers for long chemistry operations, secure cleanup and lifecycle policies. Do not run multiple API workers with a file-lock-only implementation. PDB retrieval and preparation may take time; this implementation does not promise background notifications or scheduling.

Existing baseline browser automation test failures remain separate. Unit tests use fake RCSB and compute outputs. A real known crystal reference, reviewed protonation and pose-recovery benchmark must be tested before treating this workflow as scientifically validated. Redocking RMSD is not an experimental affinity measurement.
