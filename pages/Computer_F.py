"""PanDoc Computer F direct runtime client (prepared PDBQT docking)."""
import base64
import json
import os

import requests
import streamlit as st

st.set_page_config(page_title="PanDoc · Computer F", page_icon="🧬", layout="wide")
st.title("Computer F · Direct Remote Runtime")
st.caption("No queue · Direct Vina execution · Live output · Prepared PDBQT inputs")

def setting(name):
    try:
        return str(st.secrets.get(name, os.environ.get(name, ""))).strip()
    except Exception:
        return os.environ.get(name, "").strip()

url = setting("PANDOC_COMPUTER_F_URL").rstrip("/")
key = setting("PANDOC_COMPUTER_F_API_KEY")
if not url or not key:
    st.warning("Configure PANDOC_COMPUTER_F_URL and PANDOC_COMPUTER_F_API_KEY in Streamlit secrets.")
    st.stop()
if not url.startswith("https://"):
    st.error("Computer F requires an HTTPS endpoint.")
    st.stop()

try:
    health = requests.get(url + "/health", headers={"X-API-Key": key}, timeout=8)
    health.raise_for_status()
    st.success("Computer F connected · Direct execution · No queue")
except requests.RequestException:
    st.error("Computer F is unavailable or credentials are invalid.")
    st.stop()

with st.form("computer_f_dock"):
    receptor = st.file_uploader("Prepared receptor (.pdbqt)", type=["pdbqt"], key="f_rec")
    ligand = st.file_uploader("Prepared ligand (.pdbqt)", type=["pdbqt"], key="f_lig")
    a, b, c = st.columns(3)
    cx = a.number_input("Center X (Å)", value=0.0)
    cy = b.number_input("Center Y (Å)", value=0.0)
    cz = c.number_input("Center Z (Å)", value=0.0)
    d, e, f = st.columns(3)
    sx = d.number_input("Size X (Å)", min_value=1.0, max_value=60.0, value=20.0)
    sy = e.number_input("Size Y (Å)", min_value=1.0, max_value=60.0, value=20.0)
    sz = f.number_input("Size Z (Å)", min_value=1.0, max_value=60.0, value=20.0)
    x, y, z = st.columns(3)
    exhaustiveness = x.number_input("Exhaustiveness", min_value=1, max_value=64, value=8)
    poses = y.number_input("Number of poses", min_value=1, max_value=20, value=9)
    seed = z.number_input("Seed", min_value=1, max_value=2147483647, value=2026)
    submitted = st.form_submit_button("Run on Computer F", type="primary")

if submitted:
    if not receptor or not ligand:
        st.error("Upload both prepared PDBQT files.")
        st.stop()
    logs = st.empty()
    progress = st.empty()
    lines = []
    try:
        with requests.post(
            url + "/v1/dock",
            headers={"X-API-Key": key},
            files={"receptor": (receptor.name, receptor.getvalue()),
                   "ligand": (ligand.name, ligand.getvalue())},
            data={"center_x": cx, "center_y": cy, "center_z": cz,
                  "size_x": sx, "size_y": sy, "size_z": sz,
                  "exhaustiveness": exhaustiveness, "poses": poses, "seed": seed},
            stream=True, timeout=(15, 3700),
        ) as response:
            if response.status_code == 409:
                st.warning("Computer F is busy. No queue is used; retry when free.")
                st.stop()
            response.raise_for_status()
            progress.info("Direct execution started")
            completed = False
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                item = json.loads(line)
                if item["type"] == "log":
                    lines.append(item["message"])
                    logs.code("\n".join(lines[-100:]), language="text")
                elif item["type"] == "complete":
                    st.session_state["computer_f_bundle"] = base64.b64decode(item["zip_base64"])
                    completed = True
                    progress.success("Completed on Computer F")
                elif item["type"] == "error":
                    progress.error("Computer F: " + item["message"])
            if not completed:
                st.warning("No completed result was received.")
    except (requests.RequestException, ValueError, KeyError) as exc:
        st.error("Computer F connection or response error: " + str(exc)[:300])

if st.session_state.get("computer_f_bundle"):
    st.download_button("Download Computer F results",
                       st.session_state["computer_f_bundle"],
                       file_name="computer_f_results.zip",
                       mime="application/zip")
