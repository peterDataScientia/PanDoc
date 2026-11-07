# Computer F — Direct Remote Runtime (no queue)

Computer F is an **opt-in, independent** remote execution endpoint. The initial implementation accepts prepared PDBQT receptor and ligand files and runs AutoDock Vina directly, streaming its stdout back to the PanDoc Streamlit page. It does not replace Computer A/B/C/D/E.

**Status:** initial Vina-only integration. It is not yet a full PanDoc scientific kernel, a Jupyter/Colab kernel connection, or a deployed service. No real remote docking run has been validated.

## Start the runtime on a machine you control

Use a private server / Windows, Linux or VM with a working `vina` executable. Do not use a Google Colab Free hosted runtime as a public Streamlit backend; Colab's terms and runtime lifecycle are unsuitable for that use.

Install dependencies in Python 3.11 (existing PanDoc environment or a minimal separate environment):

```bash
python -m pip install "fastapi>=0.115,<1" "uvicorn>=0.30,<1" "python-multipart>=0.0.9,<1"
```

Configure a strong, random **shared secret** in the runtime environment. For example, in PowerShell:

```powershell
$env:PANDOC_F_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:PANDOC_F_VINA = "C:\Path\To\vina.exe"
$env:PANDOC_F_MAX_CONCURRENT = "1"
python -m uvicorn computer_f_runtime:app --host 127.0.0.1 --port 8765
```

Expose the service only through an **authenticated HTTPS reverse proxy or trusted tunnel** that can reach this host from Streamlit Community Cloud, and restrict access. Tailscale-only IPs are generally not directly reachable from Streamlit Community Cloud. Never expose plain HTTP, print your secret in logs or commit it to GitHub.

In Streamlit secrets, configure:

```toml
PANDOC_COMPUTER_F_URL = "https://your-secure-computer-f-endpoint.example"
PANDOC_COMPUTER_F_API_KEY = "same-secret-as-runtime"
```

The new Streamlit page **Computer F** then presents a prepared PDBQT docking form, live output and a results download.

## Architecture

PanDoc Streamlit page → HTTPS POST `/v1/dock` → Computer F subprocess → streamed NDJSON logs → ZIP result.

- **No queue.** If all configured execution slots are occupied, requests fail with HTTP 409 and a busy message; they are not stored.
- The execution request includes box coordinates/size, exhaustiveness, number of poses and seed.
- Each request runs in an isolated temporary directory and receives an output PDBQT plus a JSON manifest.
- The endpoint accepts only a predefined Vina operation, **not** arbitrary submitted Python/shell commands.
- An API key is required for all endpoints, including health checks.
- Each input is capped at 25 MB; the output PDBQT at 25 MB; runtime timeout defaults to 3600 seconds.
- The client currently supports **one ligand per run** and prepared input files only. This is not yet integrated with the main docking results, validation or preparation UI.
- Live stdout streaming depends on the reverse proxy supporting streaming rather than buffering.
- Jobs can be lost on app disconnect or host restart. There is no persistent history.

For a full scientific runtime, the next implementation would add explicit, validated scientific operations, authenticated per-user execution isolation and structured results; no general-purpose remote code execution should be exposed to public users.

## API contract

`GET /health` requires `X-API-Key` and returns direct/no-queue status.

`POST /v1/dock` requires `X-API-Key`, multipart `receptor` and `ligand` files, `center_x/y/z`, `size_x/y/z`, `exhaustiveness`, `poses`, `seed`. Response is NDJSON lines of types `started`, `log`, `heartbeat`, `complete` (ZIP base64) or `error`. HTTP 409 means busy.

## Safety and reproducibility limitations

PDBQT validation is deliberately preliminary (non-empty and recognizable coordinate records), not chemical validation. Redocking thresholds, protonation handling, RMSD and receptor preparation remain in the existing PanDoc workflow. Before research use, verify Vina outputs and benchmark against local docking using identical prepared files and settings. Do not treat an untested external runtime as validated computational infrastructure.
