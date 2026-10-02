import json
from pathlib import Path
import pytest
from rdkit import Chem
from streamlit.testing.v1 import AppTest
from pandoc import pdb_search


def test_search_filters_and_empty_result(monkeypatch):
    captured = {}
    def fake(url, **kwargs):
        captured.update(json.loads(kwargs['params']['json']))
        return {}
    monkeypatch.setattr(pdb_search, 'get_json', fake)
    result = pdb_search.search('plasmepsin', organism='Plasmodium falciparum', resolution=2.5, ligand_only=True, start=10)
    assert result['ids'] == [] and result['total'] == 0
    assert captured['request_options']['paginate'] == dict(start=10, rows=10)
    assert captured['request_options']['results_content_type'] == ['experimental']
    attrs = {n.get('parameters', {}).get('attribute') for n in captured['query']['nodes']}
    assert 'rcsb_entry_info.resolution_combined' in attrs
    assert 'rcsb_entry_info.nonpolymer_entity_count' in attrs
    assert 'rcsb_entity_source_organism.ncbi_scientific_name' in attrs


def test_identifier_validation():
    assert pdb_search.identifier(' 1lf2 ') == '1LF2'
    assert pdb_search.identifier('pdb_00001lf2') == 'PDB_00001LF2'
    for invalid in ['../../file', 'not an id', '']:
        with pytest.raises(ValueError):
            pdb_search.identifier(invalid)


def test_entity_details_keep_chain_specific_organisms(monkeypatch):
    monkeypatch.setattr(pdb_search, 'entry', lambda _: dict(rcsb_entry_container_identifiers=dict(polymer_entity_ids=['1'], non_polymer_entity_ids=['2'])))
    def fake(url):
        if '/nonpolymer_entity/' in url:
            return dict(pdbx_entity_nonpoly=dict(comp_id='R37', name='Inhibitor'), rcsb_nonpolymer_entity_container_identifiers=dict(auth_asym_ids=['A']))
        return dict(rcsb_polymer_entity=dict(pdbx_description='Plasmepsin 2'),
            rcsb_polymer_entity_container_identifiers=dict(auth_asym_ids=['A']),
            rcsb_entity_source_organism=[dict(ncbi_scientific_name='Plasmodium falciparum')], entity_poly=dict(pdbx_mutation='S205'))
    monkeypatch.setattr(pdb_search, 'get_json', fake)
    result = pdb_search.details('1LF2')
    assert result['proteins'][0]['Organism'] == 'Plasmodium falciparum'
    assert result['proteins'][0]['Mutations'] == 'S205'
    assert result['ligands'][0]['ID'] == 'R37'


def test_search_review_load_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb_search, 'search', lambda **kwargs: dict(ids=['1LF2'], total=1, query={'fixture': True}))
    monkeypatch.setattr(pdb_search, 'summaries', lambda ids: [dict(PDB='1LF2', Title='Test complex')])
    monkeypatch.setattr(pdb_search, 'details', lambda _: dict(entry={'struct': {'title': 'Test complex'}}, proteins=[], ligands=[], warnings=[]))
    # A valid coordinate file, passed through the same normalization as a fetched mmCIF.
    import gemmi
    peptide = Chem.MolFromSequence('AG')
    from rdkit.Chem import AllChem
    AllChem.EmbedMolecule(peptide, randomSeed=2)
    structure = gemmi.read_pdb_string(Chem.MolToPDBBlock(peptide))
    cif = structure.make_mmcif_document().as_string()
    monkeypatch.setattr(pdb_search, 'download', lambda _: (cif, {'type': 'RCSB PDB', 'pdb_id': '1LF2', 'retrieved_utc': 'fixture'}))
    app = AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py').run()
    app.session_state.root = str(tmp_path)
    next(r for r in app.radio if r.label == 'Structure source').set_value('Search PDB').run()
    next(t for t in app.text_input if t.label == 'Search term').set_value('plasmepsin')
    next(b for b in app.button if b.label == 'Search PDB').click().run()
    assert not list(app.exception)
    next(b for b in app.button if b.label == 'View structure details').click().run()
    next(t for t in app.text_area if t.label == 'Why choose this structure?').set_value('Reviewed target identity')
    next(b for b in app.button if b.label == 'Load this structure').click().run()
    assert not list(app.error) and not list(app.exception)
    assert app.session_state.structure_source['selection_rationale'] == 'Reviewed target identity'
    assert (tmp_path/'source_original.cif').read_text() == cif
    manifest = json.loads((tmp_path/'experiment.json').read_text())
    assert manifest['structure_source']['pdb_id'] == '1LF2'
    assert any(b.label == 'Save component selection' for b in app.button)


def test_download_size_limit_and_provenance(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, _): yield b'data_1LF2\n#\n'
    monkeypatch.setattr(pdb_search.requests, 'get', lambda *args, **kwargs: Response())
    text, source = pdb_search.download('1lf2')
    assert text.startswith('data_')
    assert source['pdb_id'] == '1LF2'
    assert source['url'] == 'https://files.rcsb.org/download/1LF2.cif'
    assert 'retrieved_utc' in source
    monkeypatch.setattr(pdb_search, 'MAX_BYTES', 2)
    with pytest.raises(ValueError, match='25-MB'):
        pdb_search.download('1LF2')
