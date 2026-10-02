"""RCSB discovery and structure provenance, independent of preparation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import re
import requests

DATA = 'https://data.rcsb.org/rest/v1/core/'
MAX_BYTES = 25 * 1024 * 1024


def identifier(value):
    value = value.strip().upper()
    if not re.fullmatch(r'(?:[0-9][A-Z0-9]{3}|PDB_[A-Z0-9]{8})', value):
        raise ValueError('Enter a PDB ID such as 1LF2 or an extended ID such as pdb_00001lf2.')
    return value


def get_json(url, **kwargs):
    try:
        response = requests.get(url, timeout=(5, 12), **kwargs)
        if response.status_code == 204:
            return {}
        if response.status_code == 404:
            raise ValueError('This PDB entry is unavailable. Check the ID or select another entry.')
        if response.status_code == 400:
            raise ValueError('RCSB rejected these search criteria. Try Keywords or a PDB ID and simplify the filters.')
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        raise RuntimeError('RCSB could not be reached or returned an unreadable response. Retry or upload a local structure.') from exc


def search(query, mode='Keywords', organism='', method='X-RAY DIFFRACTION', resolution=None, ligand_only=False, start=0, rows=10):
    query = query.strip()
    if not query:
        raise ValueError('Enter a protein name, keyword, identifier or ligand name.')
    if mode == 'PDB ID':
        return {'ids': [identifier(query)], 'total': 1, 'query': {'pdb_id': identifier(query)}}
    attributes = {'Protein name': 'rcsb_polymer_entity.pdbx_description',
                  'UniProt accession': 'rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession',
                  'Ligand name / ID': 'rcsb_nonpolymer_entity.pdbx_description'}
    def term(attribute, operator, value):
        return dict(type='terminal', service='text', parameters=dict(attribute=attribute, operator=operator, value=value))
    if mode == 'Keywords':
        node = dict(type='terminal', service='full_text', parameters=dict(value=query))
    elif mode == 'Ligand name / ID' and re.fullmatch('[A-Za-z0-9]{3}', query):
        node = term('rcsb_nonpolymer_entity_container_identifiers.nonpolymer_comp_id', 'exact_match', query.upper())
    else:
        node = term(attributes[mode], 'exact_match' if mode == 'UniProt accession' else 'contains_phrase', query.upper() if mode == 'UniProt accession' else query)
    nodes = [node]
    if mode == 'UniProt accession':
        nodes.append(term('rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name', 'exact_match', 'UniProt'))
    if organism.strip():
        nodes.append(term('rcsb_entity_source_organism.ncbi_scientific_name', 'contains_phrase', organism.strip()))
    if method != 'Any experimental method':
        nodes.append(term('exptl.method', 'exact_match', method))
    if resolution is not None:
        nodes.append(term('rcsb_entry_info.resolution_combined', 'less_or_equal', float(resolution)))
    if ligand_only:
        nodes.append(term('rcsb_entry_info.nonpolymer_entity_count', 'greater', 0))
    payload = dict(query=dict(type='group', logical_operator='and', nodes=nodes), return_type='entry',
                   request_options=dict(paginate=dict(start=int(start), rows=int(rows)), results_content_type=['experimental']))
    result = get_json('https://search.rcsb.org/rcsbsearch/v2/query', params={'json': json.dumps(payload)})
    return dict(ids=[r['identifier'] for r in result.get('result_set', [])], total=result.get('total_count', 0), query=payload)


def entry(pdb_id):
    return get_json(DATA+'entry/'+identifier(pdb_id))


def summary(pdb_id):
    try:
        data = entry(pdb_id)
        return dict(PDB=pdb_id, Title=data.get('struct', {}).get('title', 'Not available'),
                    Method=', '.join(x['method'] for x in data.get('exptl', [])),
                    Resolution=' / '.join(str(x) for x in data.get('rcsb_entry_info', {}).get('resolution_combined', [])) or 'Not available',
                    Ligands=str(data.get('rcsb_entry_info', {}).get('nonpolymer_entity_count', 0))+' component types')
    except (ValueError, RuntimeError):
        return dict(PDB=pdb_id, Title='Metadata unavailable — retry details', Method='', Resolution='', Ligands='')


def summaries(ids):
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(summary, ids))


def details(pdb_id):
    data = entry(pdb_id)
    proteins, ligands, warnings = [], [], []
    ids = data.get('rcsb_entry_container_identifiers', {})
    for entity in ids.get('polymer_entity_ids', []):
        try:
            record = get_json(DATA+f'polymer_entity/{pdb_id}/{entity}')
            source = record.get('rcsb_entity_source_organism', [])
            proteins.append(dict(Entity=entity, Chains=', '.join(record.get('rcsb_polymer_entity_container_identifiers', {}).get('auth_asym_ids', [])),
                Molecule=record.get('rcsb_polymer_entity', {}).get('pdbx_description', 'Not available'),
                Organism=', '.join(dict.fromkeys(x.get('ncbi_scientific_name', 'Not available') for x in source)) or 'Not available',
                Mutations=record.get('entity_poly', {}).get('pdbx_mutation') or 'None reported'))
        except (ValueError, RuntimeError):
            warnings.append(f'Entity {entity} details unavailable.')
    for entity in ids.get('non_polymer_entity_ids', []):
        try:
            record = get_json(DATA+f'nonpolymer_entity/{pdb_id}/{entity}')
            info = record.get('pdbx_entity_nonpoly', {})
            ligands.append(dict(ID=info.get('comp_id', 'Not available'), Name=info.get('name', 'Not available'),
                                Chains=', '.join(record.get('rcsb_nonpolymer_entity_container_identifiers', {}).get('auth_asym_ids', []))))
        except (ValueError, RuntimeError):
            warnings.append(f'Ligand entity {entity} details unavailable.')
    return dict(entry=data, proteins=proteins, ligands=ligands, warnings=warnings)


def download(pdb_id):
    pdb_id = identifier(pdb_id)
    url = f'https://files.rcsb.org/download/{pdb_id}.cif'
    try:
        with requests.get(url, timeout=(5, 20), stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('This structure exceeds the 25-MB download limit. Use a curated smaller complex.')
                chunks.append(chunk)
        text = b''.join(chunks).decode('utf-8')
    except (requests.RequestException, UnicodeDecodeError) as exc:
        raise RuntimeError('Structure download failed. Retry or upload a local mmCIF file.') from exc
    if not text.lstrip().startswith('data_'):
        raise ValueError('RCSB did not return a valid mmCIF structure.')
    return text, dict(type='RCSB PDB', pdb_id=pdb_id, url=url, retrieved_utc=datetime.now(timezone.utc).isoformat())
