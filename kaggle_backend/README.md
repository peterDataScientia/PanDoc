# PanDoc Kaggle direct backend

This turns a **running Kaggle CPU session** into a warm PanDoc docking worker. It is intentionally a session backend, not permanent hosting. When the Kaggle session ends, the endpoint disappears.

## Kaggle setup

1. Create a Kaggle notebook with **Internet enabled**.
2. Add a Kaggle secret named `PANDOC_KAGGLE_API_KEY` with a long random value.
3. In the notebook run:

```bash
!git clone -q https://github.com/peterDataScientia/PanDoc.git
%cd PanDoc
!pip install -q -r requirements.txt
!python -u -m kaggle_backend.run_kaggle
```

The last command prints a temporary `https://...trycloudflare.com` URL. Keep the cell running.

## Streamlit secrets

Add the printed endpoint and the **same** API key to the PanDoc Streamlit secrets:

```toml
PANDOC_KAGGLE_URL = "https://example.trycloudflare.com"
PANDOC_KAGGLE_API_KEY = "your-long-random-key"
```

Then choose **Kaggle · direct warm backend** in PanDoc.

PanDoc sends the receptor, ligand PDBQT files, reference SDF when present, and docking configuration directly over HTTPS to the warm Kaggle FastAPI service. The Kaggle service starts `pandoc.worker`, exposes status/logs/cancellation, and returns a portable ZIP when complete.

## Important limits

- This is a convenience compute route for a live Kaggle session, not a permanent public API.
- Quick Tunnel URLs change after restart. Update `PANDOC_KAGGLE_URL` when that happens.
- Do not put the API key in the repository or notebook output.
- GitHub Actions remains the durable fallback if the Kaggle session is offline.
