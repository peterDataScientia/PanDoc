"""PDF-to-SMILES structure curation page for PanDoc.

Scaffold and R-group transcription is human-reviewed. This tool does not
interpret chemical drawings automatically or verify assignments against a paper.
Independent implementation inspired by the workflow in nyelidl/pdf2smi.
"""
import io
import re

import pandas as pd
import streamlit as st
from rdkit import Chem
from rdkit.Chem import Descriptors, Draw, rdMolDescriptors

st.set_page_config(page_title="PanDoc · PDF to SMILES", page_icon="🧪", layout="wide")
st.title("PDF → SMILES")
st.caption("Curate compound series from published scaffold and R-group tables. Review the chemical structures against the source before using them for modelling.")

with st.expander("1 · Inspect source PDF", expanded=True):
    upload = st.file_uploader("Paper or supplementary PDF", type=["pdf"], key="pdf2smi_upload")
    if upload:
        try:
            import fitz
            data = upload.getvalue()
            with fitz.open(stream=data, filetype="pdf") as doc:
                number = st.number_input("Page", min_value=1, max_value=len(doc), value=1, step=1)
                page = doc[int(number) - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                st.image(pix.tobytes("png"), caption=f"Page {number}", width="stretch")
                with st.expander("Extractable text (not chemical structure recognition)"):
                    st.text(page.get_text("text")[:25000] or "No text layer available")
        except ImportError:
            st.error("PyMuPDF is required for PDF preview. Install pymupdf.")
        except Exception as exc:
            st.error(f"PDF could not be opened: {exc}")

st.subheader("2 · Define scaffolds and fragments")
st.caption("Scaffold uses numbered dummy atoms, e.g. [*:1] and [*:2]. Fragment SMILES must start with the atom that attaches to that position.")
templates = st.data_editor(
    pd.DataFrame([{"Template": "example", "Scaffold_SMILES": "Cc1nc([*:1])[nH]c1[*:2]"}]),
    num_rows="dynamic", width="stretch", key="pdfsmi_templates")
fragments = st.data_editor(
    pd.DataFrame([{"Name": "phenyl", "SMILES": "c1ccccc1"},
                  {"Name": "4-hydroxyphenyl", "SMILES": "c1ccc(O)cc1"}]),
    num_rows="dynamic", width="stretch", key="pdfsmi_fragments")
st.subheader("3 · Compound assignments")
st.caption("Slots 1–3 correspond to [*:1]–[*:3]. Entries can be fragment names or attachment-first SMILES. Provide a traceable source/page for every compound.")
compounds = st.data_editor(
    pd.DataFrame([{"Compound_ID": "Example-1", "Template": "example",
                   "R1": "phenyl", "R2": "4-hydroxyphenyl", "R3": "",
                   "Source": "", "Page": ""}]),
    num_rows="dynamic", width="stretch", key="pdfsmi_compounds")


def assemble(scaffold_text, assignments):
    scaffold = Chem.MolFromSmiles(scaffold_text)
    if scaffold is None:
        raise ValueError("Invalid scaffold SMILES")
    slot_counts = {}
    for atom in scaffold.GetAtoms():
        if atom.GetAtomicNum() == 0:
            key = atom.GetAtomMapNum()
            if key < 1:
                raise ValueError("Scaffold has unnumbered attachment atoms")
            slot_counts[key] = slot_counts.get(key, 0) + 1
    if not slot_counts or any(count != 1 for count in slot_counts.values()):
        raise ValueError("Each attachment number must appear exactly once")
    if set(assignments) != set(slot_counts):
        raise ValueError(f"Required slots: {sorted(slot_counts)}; supplied: {sorted(assignments)}")
    combined = scaffold
    for slot, fragment in assignments.items():
        frag = Chem.MolFromSmiles(f"[*:{slot}]{fragment}")
        if frag is None:
            raise ValueError(f"Invalid fragment at slot {slot}")
        combined = Chem.CombineMols(combined, frag)
    mol = Chem.molzip(combined)
    Chem.SanitizeMol(mol)
    if any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        raise ValueError("Unresolved dummy attachment point")
    return mol


if st.button("Build and validate structures", type="primary"):
    template_dict = {}
    fragment_dict = {}
    for _, row in templates.fillna("").iterrows():
        name = str(row["Template"]).strip()
        if name:
            template_dict[name] = str(row["Scaffold_SMILES"]).strip()
    for _, row in fragments.fillna("").iterrows():
        name = str(row["Name"]).strip()
        if name:
            fragment_dict[name] = str(row["SMILES"]).strip()
    rows, molecules, legends = [], [], []
    for _, record in compounds.fillna("").iterrows():
        cid = str(record["Compound_ID"]).strip()
        if not cid:
            continue
        entry = {"Compound_ID": cid, "Template": str(record["Template"]).strip(),
                 "Source": str(record["Source"]).strip(), "Page": str(record["Page"]).strip()}
        try:
            if entry["Template"] not in template_dict:
                raise ValueError("Unknown scaffold template")
            vals = {}
            for slot in (1, 2, 3):
                raw = str(record[f"R{slot}"]).strip()
                if raw:
                    vals[slot] = fragment_dict.get(raw, raw)
            mol = assemble(template_dict[entry["Template"]], vals)
            entry.update(SMILES=Chem.MolToSmiles(mol, isomericSmiles=True),
                         Formula=rdMolDescriptors.CalcMolFormula(mol),
                         MW=round(Descriptors.MolWt(mol), 2), Status="OK", Error="")
            molecules.append(mol)
            legends.append(cid)
        except Exception as exc:
            entry.update(SMILES="", Formula="", MW="", Status="FAILED", Error=str(exc))
        rows.append(entry)
    st.session_state.pdfsmi_results = pd.DataFrame(rows)
    st.session_state.pdfsmi_mols = molecules
    st.session_state.pdfsmi_legends = legends

if "pdfsmi_results" in st.session_state:
    result = st.session_state.pdfsmi_results
    st.subheader("4 · Review and export")
    if result.empty:
        st.warning("No compound identifiers were entered.")
    else:
        a, b, c = st.columns(3)
        a.metric("Total", len(result))
        b.metric("RDKit valid", int((result.Status == "OK").sum()))
        c.metric("Failed", int((result.Status == "FAILED").sum()))
        st.dataframe(result, width="stretch", hide_index=True)
        mols = st.session_state.pdfsmi_mols
        if mols:
            st.image(Draw.MolsToGridImage(mols, legends=st.session_state.pdfsmi_legends,
                                         molsPerRow=4, subImgSize=(240, 210)))
        st.warning("RDKit validation checks chemical syntax and valence, not whether the structure, attachment position, stereochemistry, or activity matches the publication.")
        st.download_button("Download CSV", result.to_csv(index=False).encode(),
                           "pandoc_literature_smiles.csv", "text/csv", on_click="ignore")
        valid = result.loc[result.Status == "OK"]
        smi = "".join(f"{row.SMILES}\t{row.Compound_ID}\n" for row in valid.itertuples())
        st.download_button("Download SMI", smi.encode(), "pandoc_literature_smiles.smi",
                           "text/plain", on_click="ignore")
