#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${PANDOC_REPO_DIR:-/kaggle/working/PanDoc}"
ENV_DIR="${PANDOC_ENV_DIR:-/kaggle/working/pandoc311}"
MAMBA_BIN="${PANDOC_MAMBA_BIN:-/kaggle/working/micromamba}"

echo "== PanDoc Kaggle bootstrap =="
echo "Repo: ${REPO_DIR}"
echo "Env : ${ENV_DIR}"

cd /kaggle/working

if [ ! -x "${MAMBA_BIN}" ]; then
  echo "[1/5] Installing micromamba..."
  tmpdir="$(mktemp -d)"
  wget -qO- https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "${tmpdir}" bin/micromamba
  mv "${tmpdir}/bin/micromamba" "${MAMBA_BIN}"
  chmod +x "${MAMBA_BIN}"
  rm -rf "${tmpdir}"
else
  echo "[1/5] micromamba already present."
fi

if [ ! -x "${ENV_DIR}/bin/python" ]; then
  echo "[2/5] Creating Python 3.11 environment..."
  "${MAMBA_BIN}" create -y -p "${ENV_DIR}" python=3.11 pip
else
  echo "[2/5] Python environment already present."
fi

echo "[3/5] Upgrading packaging tools..."
"${ENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[4/5] Installing the complete PanDoc Python stack..."
cd "${REPO_DIR}"
"${ENV_DIR}/bin/python" -m pip install -r requirements.txt

echo "[5/5] Verifying scientific + web stack..."
"${ENV_DIR}/bin/python" - <<'PY'
import importlib
checks = [
    ("streamlit", "Streamlit"),
    ("numpy", "NumPy"),
    ("rdkit", "RDKit"),
    ("meeko", "Meeko"),
    ("vina", "AutoDock Vina"),
    ("gemmi", "Gemmi"),
    ("scipy", "SciPy"),
    ("py3Dmol", "py3Dmol"),
    ("pandas", "Pandas"),
    ("pdbfixer", "PDBFixer"),
    ("openmm", "OpenMM"),
    ("groq", "Groq"),
    ("propka", "PROPKA"),
    ("molscrub", "Molscrub"),
    ("joblib", "Joblib"),
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("multipart", "python-multipart"),
    ("cryptography", "Cryptography"),
    ("requests", "Requests"),
]
failed = []
for module, label in checks:
    try:
        obj = importlib.import_module(module)
        version = getattr(obj, "__version__", "")
        print(f"OK  {label}" + (f" {version}" if version else ""))
    except Exception as exc:
        failed.append((label, repr(exc)))
        print(f"FAIL {label}: {exc}")
if failed:
    print("\nPanDoc bootstrap FAILED:")
    for label, err in failed:
        print(f" - {label}: {err}")
    raise SystemExit(1)
print("\nALL PANDOC PACKAGES OK")
PY

echo
echo "PanDoc Kaggle environment is ready:"
echo "  ${ENV_DIR}/bin/python"
echo
echo "Start the backend with:"
echo "  cd ${REPO_DIR}"
echo "  ${ENV_DIR}/bin/python -u -m kaggle_backend.run_kaggle"
