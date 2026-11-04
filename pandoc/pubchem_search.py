"""PubChem PUG REST ligand lookup for PanDoc Experiment.

Public CID/name search only; SMILES are validated before preparation.
No external service is required for manually supplied ligands.
"""
from __future__ import annotations

from urllib.parse import quote

import requests

_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_PROPERTIES = ("SMILES,IUPACName,Title,MolecularFormula,MolecularWeight,"
               "XLogP,TPSA,HBondDonorCount,HBondAcceptorCount,RotatableBondCount")


class PubChemLookupError(ValueError):
    """User-actionable PubChem lookup failure."""


def find_compound(term: str) -> dict:
    """Find an exact CID or a name-resolved PubChem compound."""
    term = term.strip()
    if not term or len(term) > 160:
        raise PubChemLookupError("Enter a compound name or PubChem CID (up to 160 characters).")
    namespace = "cid" if term.isdecimal() else "name"
    if namespace == "cid" and int(term) < 1:
        raise PubChemLookupError("PubChem CID must be a positive integer.")
    encoded = str(int(term)) if namespace == "cid" else quote(term, safe="")
    url = f"{_BASE}/compound/{namespace}/{encoded}/property/{_PROPERTIES}/JSON"
    try:
        response = requests.get(url, timeout=(5, 15), headers={"Accept": "application/json"})
        if response.status_code == 404:
            raise PubChemLookupError("No PubChem compound matched this name or CID.")
        if response.status_code == 429:
            raise PubChemLookupError("PubChem rate limit reached. Try again later.")
        response.raise_for_status()
        data = response.json()
    except PubChemLookupError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise PubChemLookupError("PubChem request failed. Check the connection and try again.") from exc
    properties = data.get("PropertyTable", {}).get("Properties", [])
    if not properties:
        raise PubChemLookupError("PubChem returned no compound properties.")
    entry = properties[0]
    smiles = entry.get("SMILES") or entry.get("IsomericSMILES")
    if not smiles:
        raise PubChemLookupError("PubChem returned no stereochemical SMILES for this compound.")
    from rdkit import Chem
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise PubChemLookupError("The PubChem structure could not be parsed by RDKit.")
    cid = int(entry["CID"])
    name = str(entry.get("Title") or entry.get("IUPACName") or term)
    return {
        "cid": cid,
        "name": name,
        "smiles": Chem.MolToSmiles(molecule, isomericSmiles=True),
        "formula": str(entry.get("MolecularFormula") or ""),
        "weight": entry.get("MolecularWeight"),
        "iupac_name": str(entry.get("IUPACName") or ""),
        "xlogp": entry.get("XLogP"),
        "tpsa": entry.get("TPSA"),
        "hbd": entry.get("HBondDonorCount"),
        "hba": entry.get("HBondAcceptorCount"),
        "rotatable_bonds": entry.get("RotatableBondCount"),
        "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
    }


def compound_details_row(compound: dict) -> dict:
    """Use the same visible columns for search results and selected ligands."""
    return {
        "CID": compound["cid"],
        "Compound": compound["name"],
        "Molecular formula": compound.get("formula") or "—",
        "MW (g/mol)": compound.get("weight"),
        "XLogP": compound.get("xlogp"),
        "TPSA (Å²)": compound.get("tpsa"),
        "HBD": compound.get("hbd"),
        "HBA": compound.get("hba"),
        "Rotatable bonds": compound.get("rotatable_bonds"),
        "IUPAC name": compound.get("iupac_name") or "—",
        "SMILES": compound["smiles"],
        "PubChem": compound["url"],
    }
