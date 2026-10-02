from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from pandoc import core, jobs, figures, pdb_search

st.set_page_config(page_title='PanDoc · Docking workbench', page_icon='🧬', layout='wide')
st.markdown('''<style>
.stApp { background: #f7f9fc; }
h1,h2,h3 { color: #16324f; }
[data-testid="stSidebar"] { background: #eaf0f7; }
.block-container { padding-top: 2rem; }
</style>''', unsafe_allow_html=True)

if 'root' not in st.session_state:
    base = Path(os.environ.get('PANDOC_DATA_DIR', tempfile.gettempdir()))/'pandoc'
    root = base/uuid.uuid4().hex
    root.mkdir(parents=True)
    st.session_state.root = str(root)
root = Path(st.session_state.root)


def viewer(pdb=None, sdf=None, reference=None, center=None, size=None):
    import py3Dmol
    view = py3Dmol.view(width=850, height=430)
    view.setBackgroundColor('#ffffff')
    if pdb:
        view.addModel(pdb, 'pdb')
        view.setStyle({'model': 0}, {'cartoon': {'color': '#7b94ad'}})
        view.addStyle({'model': 0, 'hetflag': True}, {'stick': {'colorscheme': 'cyanCarbon'}})
    if reference:
        view.addModel(reference, 'sdf')
        view.setStyle({'model': int(bool(pdb))}, {'stick': {'colorscheme': 'greenCarbon'}})
    if sdf:
        view.addModel(sdf, 'sdf')
        # Last model is the candidate or redocked pose.
        idx = int(bool(pdb)) + int(bool(reference))
        view.setStyle({'model': idx}, {'stick': {'colorscheme': 'magentaCarbon'}})
    if center and size:
        view.addBox({'center': dict(zip('xyz', center)), 'dimensions': dict(zip('whd', size)), 'color': '#f59e0b', 'wireframe': True})
    view.zoomTo()
    st.iframe(view._make_html(), height=450)


def publication_figure(job, row):
    from rdkit import Chem
    config = json.loads((Path(job)/'config.json').read_text())
    if not config.get('reference'):
        return
    reference = next(iter(Chem.SDMolSupplier(config['reference'], removeHs=False)))
    poses = list(Chem.SDMolSupplier(str(Path(job)/row['sdf']), removeHs=False))
    pose = poses[row['rank']-1]
    if reference is None or pose is None:
        st.warning('The reference or selected pose could not be read for the figure.')
        return
    st.subheader('Publication figure · crystallographic and redocked ligand')
    rmsd = core.reference_rmsd(reference, pose)
    st.caption(f"Seed {row['seed']} · pose {row['rank']} · heavy-atom RMSD {rmsd:.3f} Å. Original coordinates; no ligand fitting.")
    st.caption('Shaded ball and stick · cyan: crystallographic · magenta: redocked. Rotate both together and adjust colors or background. Exports contain only the two color legend labels.')
    st.iframe(figures.overlay_html(reference, pose, seed=row['seed'], rank=row['rank']), height=620)
    st.caption('PNG: 3996 × 2340 pixels, 600 DPI. PDF: 6.66 × 3.90 inches with a raster molecular panel. Review the camera and labels before publication.')


def load_complex(text, suffix, provenance):
    pdb = core.normalize_structure(text, suffix)
    core.atoms(pdb)
    source_id = core.digest(pdb, text)
    if st.session_state.get('source_id') != source_id:
        for k in ('preparation_id', 'reference_path', 'receptor_path', 'validation_job', 'experiment_job',
                  'candidate_paths', 'selected_pdb', 'reference_pdb', 'selection_id', 'selection_record',
                  'center', 'size', 'preparation_record', 'reference_id', 'reference_smiles', 'reference_chemistry_source'):
            st.session_state.pop(k, None)
        for original in root.glob('source_original.*'):
            original.unlink()
        st.session_state.update(pdb=pdb, source_id=source_id)
        (root/'source.pdb').write_text(pdb)
        (root/('source_original'+suffix)).write_text(text)
    import hashlib
    provenance = dict(provenance, original_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    st.session_state.structure_source = provenance
    manifest()


@st.cache_data(ttl=3600, show_spinner=False)
def search_pdb(options, start):
    result = pdb_search.search(**options, start=start)
    result['summaries'] = pdb_search.summaries(result['ids'])
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def pdb_details(pdb_id):
    return pdb_search.details(pdb_id)


def pdb_discovery():
    st.caption('Search experimental PDB structures, read the details, then choose a complex to load.')
    with st.form('pdb_search_form'):
        mode = st.selectbox('Search by', ['Keywords', 'Protein name', 'PDB ID', 'UniProt accession', 'Ligand name / ID'])
        st.caption('PDB ID opens an exact entry and bypasses search filters. Protein name searches deposited descriptions; Keywords also searches synonyms and annotations.')
        query = st.text_input('Search term', placeholder='plasmepsin II, 1LF2, P46925 or R37')
        with st.expander('Search filters'):
            organism = st.text_input('Source organism', placeholder='Plasmodium falciparum')
            method = st.selectbox('Experimental method', ['X-RAY DIFFRACTION', 'Any experimental method', 'ELECTRON MICROSCOPY', 'SOLUTION NMR'])
            limit_resolution = st.checkbox('Limit maximum resolution')
            resolution = st.number_input('Maximum resolution (Å)', 0.5, 20.0, 3.0, 0.1)
            ligand_only = st.checkbox('Require a nonpolymer component', value=True,
                help='Includes ions, cofactors and additives; this does not guarantee a suitable redocking ligand.')
        submitted = st.form_submit_button('Search PDB', type='primary')
    if submitted:
        options = dict(query=query, mode=mode, organism=organism, method=method,
                       resolution=resolution if limit_resolution else None, ligand_only=ligand_only)
        try:
            with st.spinner('Searching RCSB PDB…'):
                result = search_pdb(options, 0)
            st.session_state.update(pdb_search_options=options, pdb_search_start=0, pdb_search_result=result)
        except (ValueError, RuntimeError) as exc:
            st.session_state.pop('pdb_search_result', None)
            st.warning(str(exc))
    result = st.session_state.get('pdb_search_result')
    if not result:
        return
    if not result['ids']:
        st.info('No matching structures. Try a broader keyword or relax the filters.')
        return
    start = st.session_state.get('pdb_search_start', 0)
    st.caption(f"{result['total']} matches · showing {start+1}–{start+len(result['ids'])}. Search relevance does not measure docking suitability.")
    st.dataframe(pd.DataFrame(result['summaries']), hide_index=True, width='stretch')
    left, right = st.columns(2)
    previous = left.button('Previous results', disabled=start == 0)
    following = right.button('Next results', disabled=start+10 >= result['total'])
    if previous or following:
        new_start = start + (10 if following else -10)
        try:
            with st.spinner('Loading results…'):
                next_result = search_pdb(st.session_state.pdb_search_options, new_start)
            st.session_state.update(pdb_search_start=new_start, pdb_search_result=next_result)
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.warning(str(exc))
    selected = st.selectbox('Structure to review', result['ids'])
    if st.button('View structure details'):
        try:
            with st.spinner('Reading structure metadata…'):
                detail = pdb_details(selected)
            st.session_state.pdb_review = dict(id=selected, detail=detail)
        except (ValueError, RuntimeError) as exc:
            st.warning(str(exc))
    review = st.session_state.get('pdb_review')
    if not review or review['id'] != selected:
        return
    detail = review['detail']
    entry = detail['entry']
    st.markdown('**'+selected+' · '+entry.get('struct', {}).get('title', '')+'**')
    info = entry.get('rcsb_entry_info', {})
    st.write('Method: '+', '.join(x['method'] for x in entry.get('exptl', [])))
    st.write('Resolution (Å): '+(' / '.join(str(x) for x in info.get('resolution_combined', [])) or 'Not available'))
    if detail['proteins']:
        st.write('Chains and molecule identities')
        st.dataframe(pd.DataFrame(detail['proteins']), hide_index=True, width='stretch')
    if detail['ligands']:
        st.write('Nonpolymer components — review which is a suitable reference ligand')
        st.dataframe(pd.DataFrame(detail['ligands']), hide_index=True, width='stretch')
    else:
        st.info('No nonpolymer component details available. A crystallographic reference ligand is needed for redocking.')
    for warning in detail['warnings']:
        st.warning(warning)
    st.caption('Unmodeled polymer residues (whole entry): '+str(info.get('deposited_unmodeled_polymer_monomer_count', 'Not available')))
    citation = entry.get('rcsb_primary_citation', {})
    if citation.get('title'):
        st.write('Publication: '+citation['title'])
    st.markdown(f'[Structure and validation at RCSB](https://www.rcsb.org/structure/{selected})')
    for component in detail['ligands']:
        if component['ID'] != 'Not available':
            st.markdown(f"[Review {component['ID']} ligand quality at RCSB](https://www.rcsb.org/ligand-validation/{selected}/{component['ID']})")
    st.caption('Inspect ligand quality and the intended binding site. Resolution alone does not establish suitability; unavailable validation is not a pass.')
    rationale = st.text_area('Why choose this structure?', key='pdb_structure_rationale')
    if st.button('Load this structure', type='primary'):
        try:
            with st.spinner('Downloading and checking mmCIF…'):
                text, source = pdb_search.download(selected)
                source.update(search_options=st.session_state.pdb_search_options, query=result['query'],
                              selection_rationale=rationale, metadata=detail)
                load_complex(text, '.cif', source)
            st.success('Structure loaded. Inspect the complex below and save your component selection.')
        except (ValueError, RuntimeError) as exc:
            st.warning(str(exc))


def manifest():
    data = {k: st.session_state.get(k) for k in ('experiment', 'selection_record', 'preparation_id', 'preparation_record', 'center', 'size', 'reference_smiles', 'reference_chemistry_source', 'structure_source')}
    data['software'] = core.versions()
    (root/'experiment.json').write_text(json.dumps(data, indent=2))


def go_to_selection():
    st.session_state.workflow_stage = '1 · Load complex'


def go_to_validation():
    st.session_state.workflow_stage = '3 · Validate docking'


@st.cache_data(ttl=86400, show_spinner=False)
def lookup_chemistry(component):
    return core.fetch_ccd(component)


def settings(prefix):
    a, b, c = st.columns(3)
    exhaustive = a.number_input('Search exhaustiveness', 1, 64, 8, key=prefix+'ex')
    poses = b.number_input('Maximum poses', 1, 20, 9, key=prefix+'poses')
    seeds_text = c.text_input('Seeds (comma-separated)', '2026,2027,2028' if prefix=='validation' else '2026', key=prefix+'seeds')
    seeds = [int(x.strip()) for x in seeds_text.split(',')]
    return dict(exhaustiveness=int(exhaustive), poses=int(poses), seeds=seeds, cpu=min(2, os.cpu_count() or 1))


def show_job(directory):
    state = jobs.status(directory)
    st.info(f"Job: {state['state']} · {Path(directory).name[:8]}")
    if state.get('error'):
        st.error(state['error'])
    if state['state'] in ('queued', 'running', 'starting'):
        st.caption('Refresh to check progress. Cancellation takes effect between docking searches.')
        if st.button('Cancel job', key=str(directory)+'cancel'):
            jobs.cancel(directory)
        if st.button('Refresh status', key=str(directory)+'refresh'):
            st.rerun()
    log = Path(directory)/'worker.log'
    with st.expander('Calculation log'):
        st.code(log.read_text()[-16000:] if log.exists() else 'Waiting for worker.')
    return state


with st.sidebar:
    st.title('PanDoc')
    st.caption('Prepare · Validate · Dock')
    st.text_input('Experiment name', 'My docking experiment', key='experiment')
    stage = st.radio('Workflow', ['1 · Load complex', '2 · Prepare structures', '3 · Validate docking', '4 · Run experiment', '5 · Explore results'], key='workflow_stage')
    st.divider()
    st.caption('✓ Complex loaded' if st.session_state.get('pdb') else '○ Load a complex')
    st.caption('✓ Receptor prepared' if st.session_state.get('preparation_id') else '○ Prepare receptor')
    st.caption('✓ Reference prepared' if st.session_state.get('reference_path') else '○ Prepare reference')
    st.caption('Coordinates in Å · Vina scores in kcal/mol')
    if st.button('Start a new experiment'):
        st.session_state.clear()
        st.rerun()

st.title(stage.split(' · ')[1])

try:
    if stage.startswith('1'):
        st.write('Upload a complex, inspect its components and select the receptor and crystallographic reference ligand.')
        source_mode = st.radio('Structure source', ['Upload file', 'Search PDB'], horizontal=True)
        if source_mode == 'Upload file':
            upload = st.file_uploader('PDB or mmCIF complex', type=['pdb', 'cif', 'mmcif'])
            if upload:
                load_complex(upload.getvalue().decode('utf-8'), Path(upload.name).suffix,
                             dict(type='Uploaded file', filename=upload.name))
        else:
            pdb_discovery()
        if st.session_state.get('pdb'):
            pdb = st.session_state.pdb
            source = st.session_state.get('structure_source', {})
            st.caption('Loaded structure: '+str(source.get('pdb_id', source.get('filename', 'Local structure'))))
            rows = core.inspect(pdb)
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            viewer(pdb=pdb)
            proteins = [r['residue'] for r in rows if r['kind']=='Protein']
            chains = sorted({r['chain'] for r in rows if r['kind']=='Protein'})
            selected_chains = st.multiselect('Receptor chains', chains, default=chains)
            other = [r['residue'] for r in rows if r['kind']!='Protein']
            retained = st.multiselect('Retain waters, ions or cofactors', other)
            ref_options = [r['residue'] for r in rows if r['kind']=='Other component']
            ref = st.selectbox('Crystallographic reference ligand', ['None']+ref_options)
            excluded_reference = ref in retained
            if excluded_reference:
                st.info(f'{ref} will be saved separately for redocking and excluded from the receptor.')
            retained = [residue for residue in retained if residue != ref]
            alt = st.selectbox('Default alternate conformation', ['A', 'B', 'C'])
            overrides = {}
            with st.expander('Select alternate conformations by residue'):
                for r in rows:
                    if r['alternatives']:
                        options = r['alternatives'].split(',')
                        overrides[r['residue']] = st.selectbox(r['residue'], options, key='alt'+r['residue'])
            if st.button('Save component selection', type='primary'):
                chosen = [r['residue'] for r in rows if r['kind']=='Protein' and r['chain'] in selected_chains]+retained
                receptor = core.select(pdb, chosen, alt, overrides)
                reference = core.select(pdb, [ref], alt, overrides) if ref!='None' else None
                selection = dict(chains=selected_chains, retained=retained, reference=ref, alternate=alt, overrides=overrides, reference_excluded_from_receptor=excluded_reference)
                selection_id = core.digest(receptor, reference, selection)
                if st.session_state.get('selection_id') != selection_id:
                    for k in ('preparation_id', 'reference_path', 'receptor_path', 'validation_job', 'reference_chemistry_source', 'reference_smiles', 'reference_id'):
                        st.session_state.pop(k, None)
                st.session_state.update(selected_pdb=receptor, reference_pdb=reference, selection_record=selection, selection_id=selection_id)
                (root/'selected_receptor.pdb').write_text(receptor)
                if reference:
                    (root/'reference_original.pdb').write_text(reference)
                    center, size = core.box(reference)
                    st.session_state.update(center=center, size=size)
                manifest()
                st.success('Selection saved. Continue to Prepare structures.')

    elif stage.startswith('2'):
        if not st.session_state.get('selected_pdb'):
            st.info('Save a component selection in Load complex first.')
        else:
            protein_tab, ligand_tab = st.tabs(['Receptor', 'Reference ligand'])
            with protein_tab:
                pdb = st.session_state.selected_pdb
                rows = core.inspect(pdb)
                incomplete = [r for r in rows if r['missing_estimate']]
                if incomplete:
                    st.warning('Potentially incomplete residues detected. Counts are a preliminary screen; Meeko performs the chemical template check.')
                    st.dataframe(pd.DataFrame(incomplete), hide_index=True)
                st.caption('Missing loops are not automatically reconstructed. Unmatched residues are not automatically deleted.')
                repair = st.checkbox('Rebuild missing heavy atoms with PDBFixer')
                intended_ph = st.number_input('Intended preparation pH (recorded context)', 0.0, 14.0, 7.0, 0.1)
                st.caption('Recording pH does not predict residue states. Select states using reviewed template assignments or upload a curated receptor.')
                curated = st.file_uploader('Optional curated receptor PDB', type=['pdb'], key='curated')
                templates = st.text_input('Meeko residue template assignments', placeholder='A:17=HID,A:32=ASH')
                notes = st.text_area('Preparation rationale', placeholder='Explain protonation, retained components and structural repairs.')
                confirm = st.checkbox('I reviewed the receptor components and intended protonation states.')
                if st.button('Prepare receptor', type='primary', disabled=not confirm):
                    final = curated.getvalue().decode() if curated else pdb
                    core.atoms(final)
                    if repair:
                        final = core.repair_heavy_atoms(final)
                    prep_id = core.digest(final, templates, intended_ph, core.versions())
                    directory = root/'preparations'/prep_id
                    with st.spinner('Checking chemistry and preparing receptor…'):
                        path = core.prepare_receptor(final, directory, templates)
                    st.session_state.update(receptor_path=str(path), prepared_pdb=(directory/'receptor_prepared.pdb').read_text(), preparation_id=prep_id,
                        preparation_record=dict(pH_context=intended_ph, templates=templates, repaired_heavy_atoms=repair, curated_upload=curated.name if curated else None, rationale=notes))
                    manifest()
                    st.success('Meeko preparation completed. Inspect the prepared structure below.')
                if st.session_state.get('receptor_path'):
                    path = Path(st.session_state.receptor_path)
                    prepared = path.parent/'receptor_prepared.pdb'
                    prepared_text=prepared.read_text() if prepared.exists() else st.session_state.prepared_pdb
                    comparison=[]
                    for label,text in [('Selected input',st.session_state.selected_pdb),('Prepared receptor',prepared_text)]:
                        aa=core.atoms(text)
                        comparison.append(dict(structure=label,atoms=len(aa),heavy_atoms=sum(a['element'] not in ('H','D') for a in aa),residues=len({core.key(a) for a in aa})))
                    st.dataframe(pd.DataFrame(comparison),hide_index=True)
                    viewer(pdb=prepared_text)
                    st.download_button('Download receptor PDBQT', path.read_bytes(), 'receptor.pdbqt')
                    with st.expander('Meeko preparation log'):
                        st.code((path.parent/'preparation.log').read_text())
            with ligand_tab:
                ref = st.session_state.get('reference_pdb')
                if not ref:
                    st.info('Select a crystallographic reference ligand in Load complex for redocking validation.')
                else:
                    from rdkit import Chem
                    from rdkit.Chem import Draw, rdMolDescriptors
                    aa=core.atoms(ref)
                    component=aa[0]['res']
                    residue=core.key(aa[0])
                    identity=core.digest(ref)
                    input_key='reference_input_'+identity
                    definition_key='ccd_definition_'+identity
                    st.subheader('Reference ligand: '+residue)
                    st.write('Find its PDB chemical definition, review the molecule, then prepare it using the original crystal coordinates.')
                    left,right=st.columns([2,1])
                    if left.button('Find ligand chemistry from PDB', type='primary'):
                        try:
                            with st.spinner('Finding '+component+' in the Chemical Component Dictionary…'):
                                definition=lookup_chemistry(component)
                            st.session_state[definition_key]=definition
                            st.session_state[input_key]=definition['smiles']
                            st.session_state['reference_review_'+identity]=False
                            st.success('PDB chemistry loaded. Review the structure and comparison below.')
                        except ValueError as exc:
                            st.warning(str(exc))
                    right.button('Change selected ligand', on_click=go_to_selection)
                    definition=st.session_state.get(definition_key)
                    if definition:
                        st.write('**PDB component name:** '+definition['name'])
                        st.caption(f"CCD formula: {definition['formula']} · Formal charge: {definition['formal_charge']}")
                        st.markdown(f"[View {component} in RCSB PDB](https://www.rcsb.org/ligand/{component})")
                        st.caption('CCD chemistry describes the deposited component. Review its protonation and tautomer state for your experiment.')
                    smiles=st.text_input('Reference ligand isomeric SMILES — editable',key=input_key)
                    comparison=core.ligand_comparison(ref,smiles)
                    if 'smiles_heavy_atoms' in comparison:
                        elements=sorted(set(comparison['pdb_elements'])|set(comparison['smiles_elements']))
                        table=[dict(structure='Selected crystal ligand',heavy_atoms=comparison['pdb_heavy_atoms'],**{e:comparison['pdb_elements'].get(e,0) for e in elements}),
                               dict(structure='Entered SMILES',heavy_atoms=comparison['smiles_heavy_atoms'],**{e:comparison['smiles_elements'].get(e,0) for e in elements})]
                        st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch')
                        if comparison['valid']:
                            st.success(comparison['message'])
                        else:
                            st.warning(comparison['message'])
                            st.write('**Next:** retrieve the PDB chemistry above, edit the SMILES, or change the selected ligand. Hydrogens do not change heavy-atom counts.')
                        mol2d=Chem.MolFromSmiles(smiles.strip())
                        st.image(Draw.MolToImage(mol2d,size=(650,300)),caption=f'Entered chemistry · {rdMolDescriptors.CalcMolFormula(mol2d)} · Formal charge {Chem.GetFormalCharge(mol2d)}')
                    else:
                        st.info(comparison['message'])
                    if definition:
                        atom_check=core.ccd_atom_name_check(ref,definition)
                        if atom_check['reliable'] and (atom_check['missing'] or atom_check['extra']):
                            with st.expander('Inspect atom differences',expanded=True):
                                if atom_check['missing']:
                                    st.warning('CCD heavy atoms absent from selected crystal ligand: '+', '.join(atom_check['missing']))
                                    st.write('Use a complete reference ligand structure. Missing crystallographic atoms cannot be restored by adding hydrogens.')
                                if atom_check['extra']:
                                    st.warning('Selected atom names absent from CCD definition: '+', '.join(atom_check['extra']))
                                st.caption('This check compares deposited atom names. Review custom naming before interpreting missing atoms.')
                    with st.expander('Inspect selected crystal ligand'):
                        viewer(pdb=ref)
                    reviewed=st.checkbox('I reviewed the ligand identity, stereochemistry and chemical state.',key='reference_review_'+identity)
                    if st.button('Prepare reference ligand',disabled=not comparison['valid'] or not reviewed):
                        try:
                            mol = core.reference_from_pdb(ref, smiles)
                            ident = core.digest(ref, smiles.strip())
                            directory = root/'references'/ident
                            directory.mkdir(parents=True, exist_ok=True)
                            core.write_ligand(mol, directory/'reference.pdbqt')
                            source=definition if definition and definition['smiles']==smiles.strip() else {'source':'Manual SMILES','smiles':smiles.strip()}
                            st.session_state.update(reference_path=str(directory/'reference.sdf'), reference_pdbqt=str(directory/'reference.pdbqt'), reference_smiles=smiles.strip(), reference_id=ident,reference_chemistry_source=source)
                            manifest()
                            st.success('Reference prepared with original heavy-atom coordinates. Continue to Validate docking.')
                        except (ValueError, RuntimeError) as exc:
                            st.error('The ligand could not be prepared from this chemical definition. Counts alone do not establish a matching structure.')
                            st.write('Retrieve the PDB chemistry, verify the selected component, and inspect the crystal ligand for missing atoms or incorrect connectivity.')
                            with st.expander('Preparation explanation'):
                                st.text(str(exc))
                    if st.session_state.get('reference_path'):
                        if smiles.strip()!=st.session_state.get('reference_smiles'):
                            st.info('The downloaded reference below belongs to previously prepared chemistry. Prepare the edited SMILES before using it for a new validation.')
                        path = Path(st.session_state.reference_path)
                        viewer(sdf=path.read_text())
                        st.download_button('Download reference SDF', path.read_bytes(), 'reference.sdf')
                        st.button('Continue to Validate docking',on_click=go_to_validation)

    elif stage.startswith('3') or stage.startswith('4'):
        validation = stage.startswith('3')
        if not st.session_state.get('receptor_path'):
            st.info('Prepare a receptor first.')
        elif validation and not st.session_state.get('reference_path'):
            st.info('Prepare the crystallographic reference ligand first.')
        else:
            st.write('Inspect the search box and choose reproducible docking settings.')
            center = st.session_state.get('center', [0.,0.,0.])
            size = st.session_state.get('size', [20.,20.,20.])
            with st.form('box_form'):
                cols = st.columns(3)
                center = [cols[i].number_input('Center '+axis, value=float(center[i])) for i,axis in enumerate('XYZ')]
                size = [cols[i].number_input('Size '+axis+' (Å)', min_value=1., max_value=60., value=min(60.,float(size[i]))) for i,axis in enumerate('XYZ')]
                if st.form_submit_button('Save docking box'):
                    st.session_state.update(center=center, size=size)
                    manifest()
            center = st.session_state.get('center', center)
            size = st.session_state.get('size', size)
            viewer(pdb=st.session_state.prepared_pdb, center=center, size=size)
            prefix = 'validation' if validation else 'experiment'
            params = settings(prefix)
            protocol_id = core.digest(st.session_state.preparation_id, center, size, params)
            config = dict(receptor=st.session_state.receptor_path, center=center, size=size, protocol_id=protocol_id, preparation_id=st.session_state.preparation_id, **params)
            candidates = []
            if validation:
                config.update(reference=st.session_state.reference_path, reference_id=st.session_state.reference_id,
                              ligands=[dict(id='reference', name='Reference ligand', path=st.session_state.reference_pdbqt)])
                st.caption('RMSD compares heavy atoms in the fixed receptor frame with symmetry handling. Ligands are not fitted onto the reference.')
            else:
                upload = st.file_uploader('Candidate ligands (multi-molecule SDF)', type=['sdf'])
                smiles_text = st.text_area('Or one SMILES per line', placeholder='CCO ethanol')
                chemical_review = st.checkbox('I reviewed candidate protonation, stereochemistry and tautomer states.')
                if st.button('Prepare candidate ligands', disabled=not chemical_review):
                    from rdkit import Chem
                    from rdkit.Chem import rdMolDescriptors
                    mols = []
                    if upload:
                        import io
                        for i,mol in enumerate(Chem.ForwardSDMolSupplier(io.BytesIO(upload.getvalue()), removeHs=False)):
                            if mol is None:
                                raise ValueError(f'Invalid molecule in SDF record {i+1}.')
                            mols.append((mol.GetProp('_Name') if mol.HasProp('_Name') else f'Ligand {i+1}', core.molecule(sdf=Chem.MolToMolBlock(mol))))
                    for line in smiles_text.splitlines():
                        if line.strip():
                            pieces=line.split(maxsplit=1)
                            mols.append((pieces[1] if len(pieces)>1 else f'Ligand {len(mols)+1}', core.molecule(smiles=pieces[0])))
                    if not 1<=len(mols)<=25:
                        raise ValueError('Prepare between one and 25 candidate ligands.')
                    directory = root/'candidates'/uuid.uuid4().hex
                    directory.mkdir(parents=True)
                    records=[]
                    for i,(name,mol) in enumerate(mols,1):
                        ident=f'ligand_{i:03d}'
                        path=directory/(ident+'.pdbqt')
                        core.write_ligand(mol,path)
                        records.append(dict(id=ident,name=name,path=str(path),formula=rdMolDescriptors.CalcMolFormula(mol),charge=Chem.GetFormalCharge(mol)))
                    st.session_state.candidate_paths=records
                    st.success(f'{len(records)} ligands prepared.')
                candidates=st.session_state.get('candidate_paths',[])
                if candidates:
                    st.dataframe(pd.DataFrame(candidates).drop(columns=['path']),hide_index=True)
                config['ligands']=candidates
                matched = False
                for job in jobs.list_jobs(root):
                    previous=json.loads((job/'config.json').read_text())
                    if previous.get('reference') and previous.get('protocol_id')==protocol_id and previous.get('reference_id')==st.session_state.get('reference_id') and jobs.status(job)['state']=='completed':
                        matched=True
                        break
                st.info('A completed redocking run matches these exact settings.' if matched else 'No completed redocking run matches these exact settings. Review validation before interpreting docking results.')
            active = st.session_state.get(prefix+'_job')
            busy = active and jobs.status(active)['state'] in ('queued','running','starting')
            if st.button('Run redocking' if validation else 'Run docking', type='primary', disabled=bool(busy) or not config['ligands']):
                manifest()
                directory=jobs.launch(root,config)
                st.session_state[prefix+'_job']=directory
                st.rerun()
            if st.session_state.get(prefix+'_job'):
                active_job = Path(st.session_state[prefix+'_job'])
                show_job(active_job)
                if validation and (active_job/'results.json').exists():
                    figure_rows = json.loads((active_job/'results.json').read_text())
                    if figure_rows:
                        figure_index = st.selectbox('Figure pose', range(len(figure_rows)),
                            format_func=lambda i: f"Seed {figure_rows[i]['seed']} · pose {figure_rows[i]['rank']}", key='validation_figure_pose')
                        publication_figure(active_job, figure_rows[figure_index])

    else:
        all_jobs=jobs.list_jobs(root)
        if not all_jobs:
            st.info('Run redocking or a docking experiment to see results.')
        else:
            job=st.selectbox('Calculation',all_jobs,format_func=lambda p: p.name[:8]+' · '+jobs.status(p)['state'])
            state=show_job(job)
            config=json.loads((job/'config.json').read_text())
            path=job/'results.json'
            if path.exists():
                if state['state']!='completed':
                    st.warning('These are partial results from an unfinished or interrupted calculation.')
                rows=json.loads(path.read_text())
                df=pd.DataFrame(rows)
                st.dataframe(df.drop(columns=['sdf']),hide_index=True,width='stretch')
                if 'reference_rmsd_A' in df:
                    threshold=st.number_input('Pose-recovery RMSD threshold (Å)',0.1,10.,2.,0.1)
                    top=df[df['rank']==1]
                    a,b,c=st.columns(3)
                    a.metric('Best recovered RMSD',f"{df.reference_rmsd_A.min():.2f} Å")
                    b.metric('Top-ranked pose recovery',f"{int((top.reference_rmsd_A<=threshold).sum())}/{len(top)} seeds")
                    c.metric('Best-pose recovery',f"{int((df.groupby('seed').reference_rmsd_A.min()<=threshold).sum())}/{len(top)} seeds")
                    st.caption('Pose recovery tests this receptor and protocol. It does not validate experimental affinity predictions.')
                index=st.selectbox('Inspect pose',list(range(len(rows))),format_func=lambda i:f"{rows[i]['ligand']} · seed {rows[i]['seed']} · pose {rows[i]['rank']}")
                row=rows[index]
                from rdkit import Chem
                poses=list(Chem.SDMolSupplier(str(job/row['sdf']),removeHs=False))
                pose=poses[row['rank']-1]
                if pose is None:
                    raise ValueError('Selected pose could not be read from SDF.')
                sdf=Chem.MolToMolBlock(pose)+'\n$$$$\n'
                receptor=Path(config['receptor']).parent/'receptor_prepared.pdb'
                if config.get('reference'):
                    publication_figure(job, row)
                else:
                    viewer(pdb=receptor.read_text() if receptor.exists() else None, sdf=sdf)
                st.download_button('Download results CSV',df.to_csv(index=False),'results.csv','text/csv')
                st.download_button('Download selected pose SDF',sdf,'selected_pose.sdf')
            with st.expander('Saved docking settings'):
                st.json(config)
        manifest()
        st.download_button('Download complete experiment',core.bundle(root),'pandoc_experiment.zip','application/zip')

except Exception as exc:
    st.error(str(exc))
    with st.expander('Diagnostic details'):
        st.exception(exc)
