from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from pandoc import core, jobs, figures, pdb_search, structure_checks, phprep, ui

PANDOC_LOGO = Path(__file__).parent / 'assets' / 'pandoc_logo.jpg'
PANDOC_CSS = Path(__file__).parent / 'assets' / 'pandoc.css'

st.set_page_config(page_title='PanDoc · Docking workbench', page_icon='🧬', layout='wide')
ui.load_css(st, PANDOC_CSS)

# Isolated publication-export demonstration for browser regression checks.
if st.query_params.get('publication_demo') == '1':
    from rdkit import Chem
    from rdkit.Chem import AllChem
    import streamlit.components.v1 as components
    reference = Chem.AddHs(Chem.MolFromSmiles('CCOc1ccc(CC(=O)NCCO)cc1'))
    AllChem.EmbedMolecule(reference, randomSeed=2026)
    pose = Chem.Mol(reference)
    conf = pose.GetConformer()
    for i in range(pose.GetNumAtoms()):
        point = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (point.x+0.5, point.y+0.3, point.z))
    st.title('Publication export demonstration')
    st.caption('Synthetic fixture for export checks; these are not docking results.')
    components.html(figures.overlay_html(reference, pose, seed=2026, rank=1), height=620, scrolling=True)
    st.stop()

if 'root' not in st.session_state:
    base = Path(os.environ.get('PANDOC_DATA_DIR', tempfile.gettempdir()))/'pandoc'
    root = base/uuid.uuid4().hex
    root.mkdir(parents=True)
    st.session_state.root = str(root)
root = Path(st.session_state.root)


def viewer(pdb=None, sdf=None, reference=None, center=None, size=None, focus=None):
    import py3Dmol
    view = py3Dmol.view(width='100%', height=430)
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
    if pdb and focus:
        serials = [int(a['line'][6:11]) for a in core.atoms(pdb) if core.key(a)==focus]
        if serials:
            selection = {'model': 0, 'serial': serials}
            view.addStyle(selection, {'stick': {'color': '#f59e0b'}})
            view.zoomTo(selection)
    import streamlit.components.v1 as components
    html = view._make_html()
    controls = f"""<div style='padding:6px'><button onclick='viewer_{view.uniqueid}.zoom(1.2);viewer_{view.uniqueid}.render()'>Zoom +</button>
<button onclick='viewer_{view.uniqueid}.zoom(0.8);viewer_{view.uniqueid}.render()'>Zoom −</button>
<button onclick='viewer_{view.uniqueid}.zoomTo();viewer_{view.uniqueid}.render()'>Fit structure</button></div>"""
    components.html(html + controls, height=490, scrolling=False)



@st.cache_data(show_spinner=False)
def inspect_structure(pdb, raw=False):
    return structure_checks.check(pdb, raw=raw)


def structure_review(pdb, checks, widget_key):
    issues = checks['issues']
    errors = [i for i in issues if i['severity']=='Error']
    missing = [i for i in issues if i['problem'].startswith('Missing heavy atoms:')]
    st.caption(f"{checks['residue_count']} residues · {checks['heavy_atom_count']} heavy atoms")
    if errors:
        st.warning(f"{len(errors)} coordinate problems need attention. Open Structure details to inspect them.")
    elif missing:
        st.info(f"Missing atoms detected in {len(missing)} residues. Prepare receptor will rebuild and check them automatically.")
    focus = None
    show_focus = False
    with st.expander('Structure details', expanded=bool(errors)):
        if issues:
            ordered = errors + [i for i in issues if i['severity']!='Error']
            selected = st.selectbox('Inspect an issue', range(len(ordered)),
                format_func=lambda n: ordered[n]['residue']+' · '+ordered[n]['problem'], key=widget_key)
            issue = ordered[selected]
            focus = issue['residue']
            show_focus = st.checkbox('Focus on selected residue', value=bool(errors), key=widget_key+'_focus')
            st.write(issue['action'])
            st.session_state.assistant_selected_issue = issue
            st.dataframe(pd.DataFrame(ordered), hide_index=True)
        else:
            st.caption('No issues detected by these checks.')
            st.session_state.pop('assistant_selected_issue', None)
        st.caption(checks['scope'])
    viewer(pdb=pdb, focus=focus if show_focus else None)


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
    st.caption('Shaded ball and stick · cyan: crystallographic · magenta: redocked. Rotate both together. The optional rounded border, width, color and corner radius are included in PNG/PDF exports.')
    import streamlit.components.v1 as components
    components.html(figures.overlay_html(reference, pose, seed=row['seed'], rank=row['rank']), height=620, scrolling=True)
    st.caption('PNG: 3996 × 2340 pixels, 600 DPI. PDF: 6.66 × 3.90 inches with a raster molecular panel. Review the camera and labels before publication.')


def load_complex(text, suffix, provenance):
    pdb = core.normalize_structure(text, suffix)
    core.atoms(pdb)
    source_id = core.digest(pdb, text)
    if st.session_state.get('source_id') != source_id:
        for k in ('preparation_id', 'reference_path', 'receptor_path', 'validation_job', 'experiment_job',
                  'candidate_paths', 'selected_pdb', 'reference_pdb', 'selection_id', 'selection_record',
                  'assistant_diagnostic', 'assistant_structure_checks', 'assistant_selected_issue', 'structure_report', 'preparation_review', 'center', 'size', 'preparation_record', 'reference_id', 'reference_smiles', 'reference_chemistry_source'):
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
    initial = jobs.status(directory)
    active_states = ('queued', 'running', 'starting')
    polling = initial['state'] in active_states

    @st.fragment(run_every=2 if polling else None)
    def live_job():
        state = jobs.status(directory)
        # Refresh the surrounding results and run controls once the job finishes.
        if polling and state['state'] not in active_states:
            st.rerun()
        st.info(f"Job: {state['state']} · {Path(directory).name[:8]}")
        if state.get('error'):
            st.error(state['error'])
        total = state.get('total', 0)
        if total:
            completed = state.get('completed', 0)
            st.progress(min(completed / total, 1.0), text=f'{completed}/{total} docking searches completed')
        if state['state'] in active_states:
            st.caption('Updates automatically every 2 seconds. Cancellation takes effect between docking searches.')
            if st.button('Cancel job', key=str(directory)+'cancel'):
                jobs.cancel(directory)
                st.info('Cancellation requested.')
        log = Path(directory)/'worker.log'
        with st.expander('Calculation log', expanded=polling):
            st.code(log.read_text(errors='replace')[-16000:] if log.exists() else 'Waiting for worker.')
    live_job()
    return initial


if st.session_state.get('next_stage'):
    st.session_state.workflow_stage = st.session_state.pop('next_stage')

with st.sidebar:
    st.markdown(
        """<div class="pd-brand">
              <div class="pd-brand-mark">
                <span class="pd-brand-dot"></span>
                <span class="pd-brand-ring"></span>
              </div>
              <div class="pd-brand-word">Pan<span>Doc</span></div>
            </div>""",
        unsafe_allow_html=True,
    )
    st.text_input('Experiment name', 'My docking experiment', key='experiment')
    stage = st.radio('Workflow', ['1 · Load complex', '2 · Prepare structures', '3 · Validate docking', '4 · Run experiment', '5 · Explore results'], key='workflow_stage')
    st.divider()
    st.caption('✓ Complex loaded' if st.session_state.get('pdb') else '○ Load a complex')
    st.caption('✓ Receptor prepared' if st.session_state.get('preparation_id') else '○ Prepare receptor')
    st.caption('✓ Reference prepared' if st.session_state.get('reference_path') else '○ Prepare reference')
    st.markdown('<div class="pd-section-label">Scientific units</div>', unsafe_allow_html=True)
    st.caption('Coordinates in Å · Vina scores in kcal/mol')
    if st.button('Start a new experiment'):
        st.session_state.clear()
        st.rerun()

from pandoc import assistant

heading, assistant_control = st.columns([7, 3])
with assistant_control:
    st.toggle('Assistant', key='assistant_open', value=False)
if st.session_state.assistant_open:
    workspace, assistant_panel = st.columns([3, 2], gap='large')
else:
    workspace = st.container()
def show_assistant():
    if st.session_state.assistant_open:
        with assistant_panel:
            assistant.render(st, root, panel=True)

st.session_state.pop('assistant_active_job', None)
st.session_state.pop('assistant_selected_pose', None)
if stage.startswith('3') or stage.startswith('4'):
    active = st.session_state.get('validation_job' if stage.startswith('3') else 'experiment_job')
    if active:
        st.session_state.assistant_active_job = str(active)
with workspace:
    stage_title = stage.split(' · ')[1]
    ui.shell_header(st, stage_title, '')
    ui.workflow_stepper(st, stage)
    ui.status_grid(st,
        complex_loaded=bool(st.session_state.get('pdb')),
        receptor_ready=bool(st.session_state.get('preparation_id')),
        reference_ready=bool(st.session_state.get('reference_path')))
    if stage.startswith('2'):
        ui.glossary(st, [
            ('pKa', 'The pH at which an ionizable group is approximately 50% protonated.'),
            ('HID/HIE/HIP', 'Common histidine protonation/tautomer states used during receptor preparation.'),
            ('Microstate', 'A specific protonation and tautomeric state of a ligand at the selected pH.'),
        ])
    elif stage.startswith('3') or stage.startswith('4'):
        ui.glossary(st, [
            ('RMSD', 'Heavy-atom root-mean-square deviation used here to assess redocking pose recovery.'),
            ('Exhaustiveness', 'Vina search-effort parameter; larger values explore the search space more thoroughly.'),
            ('Grid box', 'The three-dimensional region in which Vina searches for ligand poses.'),
        ])

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
                inventory = structure_checks.inventory(pdb)
                rows = inventory['components']
                with st.expander('Components in this structure'):
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
                original_checks = inspect_structure(pdb, raw=True)
                st.session_state.assistant_structure_checks = original_checks
                structure_review(pdb, original_checks, 'original_issue_'+st.session_state.source_id)
                with st.expander('Recorded connections'):
                    st.caption(inventory['connection_scope'])
                    st.code('\n'.join(inventory['connections']) or 'No LINK or SSBOND records found.')
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
                alt = 'A'
                overrides = {}
                with st.expander('Alternate conformations'):
                    if not inventory['alternates']:
                        st.caption('No alternate atom positions detected.')
                    else:
                        st.caption('Choose one conformation per residue. Recommendations prefer completeness, then occupancy. Binding-site alternatives may warrant separate docking runs.')
                    for entry in inventory['alternates']:
                        options = [o['label'] for o in entry['options']]
                        details = {o['label']: o for o in entry['options']}
                        overrides[entry['residue']] = st.selectbox(entry['residue'], options,
                            index=options.index(entry['recommended']),
                            format_func=lambda label, d=details: f"{label} · occupancy {d[label]['mean_occupancy']:.2f} · {d[label]['heavy_atoms']} heavy atoms",
                            key='alt_'+st.session_state.source_id+'_'+entry['residue'])
                if st.button('Use selection and continue', type='primary'):
                    chosen = [r['residue'] for r in rows if r['kind']=='Protein' and r['chain'] in selected_chains]+retained
                    receptor = core.select(pdb, chosen, alt, overrides)
                    reference = core.select(pdb, [ref], alt, overrides) if ref!='None' else None
                    selection = dict(chains=selected_chains, retained=retained, reference=ref, alternate=alt, overrides=overrides, reference_excluded_from_receptor=excluded_reference, alternate_recommendations=inventory['alternates'])
                    selection_id = core.digest(receptor, reference, selection)
                    if st.session_state.get('selection_id') != selection_id:
                        for k in ('preparation_id', 'reference_path', 'receptor_path', 'validation_job', 'reference_chemistry_source', 'reference_smiles', 'reference_id', 'experiment_job', 'structure_report', 'assistant_selected_issue'):
                            st.session_state.pop(k, None)
                    st.session_state.update(selected_pdb=receptor, reference_pdb=reference, selection_record=selection, selection_id=selection_id)
                    (root/'selected_receptor.pdb').write_text(receptor)
                    selection_report = dict(original_checks=original_checks, selection=selection, selection_changes=structure_checks.changes(pdb, receptor), selected_checks=inspect_structure(receptor), connections=inventory['connections'])
                    (root/'selection_report.json').write_text(json.dumps(selection_report, indent=2))
                    if reference:
                        (root/'reference_original.pdb').write_text(reference)
                        center, size = core.box(reference)
                        st.session_state.update(center=center, size=size)
                    manifest()
                    st.session_state.next_stage = '2 · Prepare structures'
                    st.rerun()

        elif stage.startswith('2'):
            if not st.session_state.get('selected_pdb'):
                st.info('Choose the receptor in Load complex, then select Use selection and continue.')
            else:
                protein_tab, ligand_tab = st.tabs(['Receptor', 'Reference ligand'])
                with protein_tab:
                    pdb = st.session_state.selected_pdb
                    ui.card(st, 'Receptor preparation', '', badge_text='pH-aware', badge_kind='running')
                    with st.expander('Advanced preparation', expanded=bool(st.session_state.get('assistant_diagnostic'))):
                        repair = st.checkbox('Rebuild missing heavy atoms with PDBFixer', value=True)
                        intended_ph = st.number_input('Preparation pH', 0.0, 14.0, 7.0, 0.1)
                        st.caption('PROPKA predicts structure-dependent residue pKa values at this pH. Review proposed residue states before receptor preparation.')
                        curated = st.file_uploader('Optional curated receptor PDB', type=['pdb'], key='curated')
                        templates = st.text_input('Additional Meeko residue template assignments', placeholder='Optional manual overrides, e.g. A:17=HID,A:32=ASH')
                        notes = st.text_area('Preparation rationale', placeholder='Optional notes about your preparation choices.')
                    st.session_state.preparation_review = dict(pH_context=intended_ph, templates=templates, rebuild_missing_atoms=repair, curated_input=bool(curated))
                    selected_input = curated.getvalue().decode() if curated else pdb
                    ph_prediction_key = core.digest(selected_input, repair, intended_ph)
                    if st.button('Analyze protonation at selected pH'):
                        try:
                            ph_input = selected_input
                            if repair:
                                with st.spinner('Repairing heavy atoms before pKa prediction…'):
                                    ph_input = core.repair_heavy_atoms(ph_input)
                            with st.spinner('Running PROPKA pKa prediction…'):
                                ph_dir = root/'protonation'/ph_prediction_key
                                rows, raw_propka = phprep.predict_protein_states(ph_input, ph_dir, intended_ph)
                            st.session_state.protonation_prediction = dict(
                                key=ph_prediction_key, pH=float(intended_ph), rows=rows,
                                propka_output=str(ph_dir/'propka_input.pka'),
                                propka_log=str(ph_dir/'propka.log'))
                            st.success(f'Predicted protonation behavior for {len(rows)} titratable residues. Review the table below.')
                        except (ValueError, RuntimeError) as exc:
                            st.session_state.pop('protonation_prediction', None)
                            st.error(str(exc))
                    prediction = st.session_state.get('protonation_prediction')
                    reviewed_templates = ''
                    if prediction and prediction.get('key') == ph_prediction_key:
                        st.markdown('### pH-aware residue-state review')
                        ui.callout(st, 'PROPKA predictions are proposals, not unquestionable assignments. Review residues near their pKa and all neutral histidines.', 'warn')
                        st.caption('Residues within ±1 pH unit of their predicted pKa are flagged for review. Neutral histidines require an explicit HID/HIE choice.')
                        display_rows = []
                        state_overrides = {}
                        for row in prediction['rows']:
                            item = dict(row)
                            residue = row['residue']
                            if row['residue_name'] == 'HIS' and row['suggested_state'].startswith('neutral'):
                                choice = st.selectbox(
                                    f'{residue} HIS tautomer',
                                    ['HID', 'HIE', 'HIP'],
                                    index=1,
                                    key='ph_state_'+ph_prediction_key+'_'+residue)
                                state_overrides[residue] = choice
                                item['selected_state'] = choice
                            elif row.get('template'):
                                options = [row['template']]
                                if row['residue_name'] == 'ASP':
                                    options = ['ASP','ASH']
                                elif row['residue_name'] == 'GLU':
                                    options = ['GLU','GLH']
                                elif row['residue_name'] == 'LYS':
                                    options = ['LYS','LYN']
                                elif row['residue_name'] == 'HIS':
                                    options = ['HIP','HID','HIE']
                                default = options.index(row['template']) if row['template'] in options else 0
                                choice = st.selectbox(
                                    f'{residue} {row["residue_name"]}',
                                    options,
                                    index=default,
                                    key='ph_state_'+ph_prediction_key+'_'+residue)
                                state_overrides[residue] = choice
                                item['selected_state'] = choice
                            else:
                                item['selected_state'] = row['suggested_state']
                            display_rows.append(item)
                        st.dataframe(pd.DataFrame(display_rows)[['residue','residue_name','pKa','pH','suggested_state','selected_state','near_pKa','review_required']], hide_index=True, width='stretch')
                        reviewed_templates = phprep.template_assignments(prediction['rows'], state_overrides)
                        st.session_state.protonation_review = dict(
                            pH=float(intended_ph), rows=display_rows,
                            template_assignments=reviewed_templates,
                            propka_output=prediction.get('propka_output'))
                        st.caption('The selected states will be passed to Meeko as explicit residue-template assignments.')
                    else:
                        st.info('Run Analyze protonation at selected pH before preparing the receptor.')
                    checks = inspect_structure(selected_input)
                    st.session_state.assistant_structure_checks = checks
                    if not st.session_state.get('receptor_path'):
                        structure_review(selected_input, checks, 'preparation_issue_'+core.digest(selected_input))
                    if st.button('Prepare receptor', type='primary', disabled=not bool(reviewed_templates)):
                        st.session_state.pop('assistant_diagnostic', None)
                        for stale in ('receptor_path', 'preparation_id', 'validation_job', 'experiment_job', 'structure_report'):
                            st.session_state.pop(stale, None)
                        final = selected_input
                        core.atoms(final)
                        if repair:
                            with st.spinner('Rebuilding and checking missing atoms…'):
                                final = core.repair_heavy_atoms(final)
                        final_checks = structure_checks.check(final)
                        blocking = [issue for issue in final_checks['issues'] if issue['severity']=='Error']
                        if blocking:
                            st.session_state.assistant_structure_checks = final_checks
                            rejected = dict(input_checks=checks, repaired_checks=final_checks, repair_changes=structure_checks.changes(selected_input, final), software_versions=core.versions())
                            (root/'rejected_structure_report.json').write_text(json.dumps(rejected, indent=2))
                            st.download_button('Download structure review report', json.dumps(rejected, indent=2), 'structure_report.json', 'application/json')
                            first = blocking[0]
                            st.error(first['residue']+': '+first['problem'])
                            st.write(first['action'])
                            st.info('Open Advanced preparation to upload corrected coordinates, then select Prepare receptor again.')
                            with st.expander('Other detected problems'):
                                st.dataframe(pd.DataFrame(blocking), hide_index=True)
                            st.session_state.assistant_diagnostic = 'Coordinate screening found blocking structure errors.'
                            show_assistant()
                            st.stop()
                        repair_changes = structure_checks.changes(selected_input, final)
                        combined_templates = ','.join(x for x in (reviewed_templates, templates.strip()) if x)
                        prep_id = core.digest(final, combined_templates, intended_ph, st.session_state.get('protonation_review'), core.versions())
                        directory = root/'preparations'/prep_id
                        with st.spinner('Checking chemistry and preparing receptor…'):
                            selection_path = root/'selection_report.json'
                            report = dict(selection_report=json.loads(selection_path.read_text()) if selection_path.exists() else {}, input_checks=checks, repaired_checks=final_checks, repair_changes=repair_changes, settings=dict(pH=intended_ph, templates=combined_templates, rationale=notes, curated_input=bool(curated), protonation_review=st.session_state.get('protonation_review')), software_versions=core.versions())
                            directory.mkdir(parents=True, exist_ok=True)
                            (directory/'structure_report.json').write_text(json.dumps(report, indent=2))
                            try:
                                path = core.prepare_receptor(final, directory, combined_templates)
                            except ValueError as exc:
                                st.session_state.assistant_diagnostic = (directory/'preparation.log').read_text()
                                st.error(str(exc).split(' Full diagnostics:')[0])
                                st.info('Open Advanced preparation to supply reviewed template assignments or a corrected receptor, then retry.')
                                with st.expander('Preparation diagnostic log'):
                                    st.code((directory/'preparation.log').read_text())
                                st.download_button('Download failed preparation report', (directory/'structure_report.json').read_bytes(), 'structure_report.json', 'application/json')
                                st.download_button('Download preparation input PDB', (directory/'receptor_input.pdb').read_bytes(), 'receptor_input.pdb')
                                show_assistant()
                                st.stop()
                            prepared_output = (directory/'receptor_prepared.pdb').read_text()
                            report.update(prepared_checks=structure_checks.check(prepared_output), preparation_changes=structure_checks.changes(final, prepared_output))
                            missing_residues = {core.key(a) for a in core.atoms(final)} - {core.key(a) for a in core.atoms(prepared_output)}
                            for residue in sorted(missing_residues):
                                report['prepared_checks']['issues'].append(dict(severity='Error', residue=residue, problem='Residue absent from prepared output', action='Review preparation; removed residues cannot be silently accepted.'))
                            for change in report['preparation_changes']:
                                if change['change']=='Removed or renamed heavy atom':
                                    report['prepared_checks']['issues'].append(dict(severity='Error', residue=change['residue'], problem='Input heavy atom absent or renamed: '+change['atom'], action='Review backend output and intended atom identity before accepting this receptor.'))
                            st.session_state.structure_report = report
                            st.session_state.assistant_structure_checks = report['prepared_checks']
                            (directory/'structure_report.json').write_text(json.dumps(report, indent=2))
                            if any(i['severity']=='Error' for i in report['prepared_checks']['issues']):
                                st.download_button('Download rejected preparation report', (directory/'structure_report.json').read_bytes(), 'structure_report.json', 'application/json')
                                raise ValueError('Prepared receptor failed coordinate validation. Inspect the saved structure report before using it.')
                        st.session_state.update(receptor_path=str(path), prepared_pdb=(directory/'receptor_prepared.pdb').read_text(), preparation_id=prep_id,
                            preparation_record=dict(pH=intended_ph, templates=combined_templates, protonation_review=st.session_state.get('protonation_review'), repaired_heavy_atoms=repair, curated_upload=curated.name if curated else None, rationale=notes))
                        manifest()
                    if st.session_state.get('receptor_path'):
                        path = Path(st.session_state.receptor_path)
                        prepared = path.parent/'receptor_prepared.pdb'
                        prepared_text=prepared.read_text() if prepared.exists() else st.session_state.prepared_pdb
                        st.success('Receptor ready for docking.')
                        viewer(pdb=prepared_text)
                        st.download_button('Download receptor PDBQT', path.read_bytes(), 'receptor.pdbqt')
                        with st.expander('Preparation details'):
                            comparison=[]
                            for label,text in [('Selected input',st.session_state.selected_pdb),('Prepared receptor',prepared_text)]:
                                aa=core.atoms(text)
                                comparison.append(dict(structure=label,atoms=len(aa),heavy_atoms=sum(a['element'] not in ('H','D') for a in aa),residues=len({core.key(a) for a in aa})))
                            st.dataframe(pd.DataFrame(comparison),hide_index=True)
                            report_path = path.parent/'structure_report.json'
                            if report_path.exists():
                                report = json.loads(report_path.read_text())
                                st.session_state.structure_report = report
                                st.session_state.assistant_structure_checks = report.get('prepared_checks', {})
                                changes = report.get('repair_changes', []) + report.get('preparation_changes', [])
                                if changes:
                                    st.dataframe(pd.DataFrame(changes), hide_index=True)
                                else:
                                    st.caption('No heavy-atom additions, removals or coordinate changes detected.')
                                post = report.get('prepared_checks', {})
                                if post.get('issues'):
                                    st.dataframe(pd.DataFrame(post['issues']), hide_index=True)
                                st.caption(post.get('scope', ''))
                                st.download_button('Download structure report', report_path.read_bytes(), 'structure_report.json', 'application/json', on_click='ignore')
                            st.caption('Preparation log')
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
                        st.markdown('### Reference ligand: '+residue)
                        ui.card(st, 'Reference ligand', '', badge_text='Review', badge_kind='review')
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
                        ligand_ph = float(st.session_state.get('preparation_review', {}).get('pH_context',
                                         st.session_state.get('preparation_record', {}).get('pH', 7.0)))
                        state_key = 'reference_microstates_'+identity+'_'+str(ligand_ph)
                        if st.button(f'Enumerate ligand states at pH {ligand_ph:.1f}'):
                            try:
                                with st.spinner('Enumerating protonation and tautomer states with Molscrub…'):
                                    states = phprep.enumerate_ligand_states(smiles, ligand_ph)
                                st.session_state[state_key] = [
                                    dict(index=s['index'], smiles=s['smiles'], formal_charge=s['formal_charge'])
                                    for s in states
                                ]
                            except (ValueError, RuntimeError) as exc:
                                st.warning(str(exc))
                        ligand_states = st.session_state.get(state_key, [])
                        selected_smiles = smiles
                        selected_state = None
                        if ligand_states:
                            st.caption(f'Molscrub generated {len(ligand_states)} unique state(s) at pH {ligand_ph:.1f}. Select the state to use while preserving crystallographic heavy-atom coordinates.')
                            chosen_state = st.selectbox(
                                'Reference ligand microstate',
                                range(len(ligand_states)),
                                format_func=lambda i: f"State {ligand_states[i]['index']} · charge {ligand_states[i]['formal_charge']:+d} · {ligand_states[i]['smiles']}",
                                key='reference_microstate_choice_'+identity+'_'+str(ligand_ph))
                            selected_state = ligand_states[chosen_state]
                            selected_smiles = selected_state['smiles']
                        comparison=core.ligand_comparison(ref,selected_smiles)
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
                            mol2d=Chem.MolFromSmiles(selected_smiles.strip())
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
                                mol = core.reference_from_pdb(ref, selected_smiles)
                                ident = core.digest(ref, selected_smiles.strip(), ligand_ph)
                                directory = root/'references'/ident
                                directory.mkdir(parents=True, exist_ok=True)
                                core.write_ligand(mol, directory/'reference.pdbqt')
                                source=definition if definition and definition['smiles']==selected_smiles.strip() else {'source':'Molscrub pH-aware state' if selected_state else 'Manual SMILES','smiles':selected_smiles.strip(),'pH':ligand_ph,'formal_charge':selected_state['formal_charge'] if selected_state else Chem.GetFormalCharge(mol)}
                                st.session_state.update(reference_path=str(directory/'reference.sdf'), reference_pdbqt=str(directory/'reference.pdbqt'), reference_smiles=selected_smiles.strip(), reference_id=ident,reference_chemistry_source=source)
                                manifest()
                                st.success('Reference prepared with original heavy-atom coordinates. Continue to Validate docking.')
                            except (ValueError, RuntimeError) as exc:
                                st.error('The ligand could not be prepared from this chemical definition. Counts alone do not establish a matching structure.')
                                st.write('Retrieve the PDB chemistry, verify the selected component, and inspect the crystal ligand for missing atoms or incorrect connectivity.')
                                with st.expander('Preparation explanation'):
                                    st.text(str(exc))
                        if st.session_state.get('reference_path'):
                            if selected_smiles.strip()!=st.session_state.get('reference_smiles'):
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
                ui.card(st, 'Docking protocol', '', badge_text='Validation' if validation else 'Experiment', badge_kind='ready' if validation else 'running')
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
                    candidate_ph = float(st.session_state.get('preparation_record', {}).get('pH', 7.0))
                    enumerate_states = st.checkbox(f'Enumerate candidate protonation/tautomer states at pH {candidate_ph:.1f} with Molscrub', value=True)
                    chemical_review = st.checkbox('I will review the generated candidate microstates before docking.')
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
                            raise ValueError('Prepare between one and 25 input candidate ligands.')
                        prepared_states = []
                        for parent_index, (name, mol) in enumerate(mols, 1):
                            if enumerate_states:
                                parent_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
                                states = phprep.enumerate_ligand_states(parent_smiles, candidate_ph)
                                for state in states:
                                    prepared_states.append((name, parent_index, state['index'], state['mol'], state['smiles']))
                            else:
                                state_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
                                prepared_states.append((name, parent_index, 1, mol, state_smiles))
                        if len(prepared_states) > 25:
                            raise ValueError(f'pH-aware enumeration generated {len(prepared_states)} states. Reduce the input set or prepare compounds in smaller batches (maximum 25 states per run).')
                        directory = root/'candidates'/uuid.uuid4().hex
                        directory.mkdir(parents=True)
                        records=[]
                        for i,(name,parent_index,state_index,mol,state_smiles) in enumerate(prepared_states,1):
                            ident=f'ligand_{i:03d}'
                            path=directory/(ident+'.pdbqt')
                            core.write_ligand(mol,path)
                            records.append(dict(
                                id=ident, name=name, parent=parent_index, microstate=state_index,
                                smiles=state_smiles, pH=candidate_ph, path=str(path),
                                formula=rdMolDescriptors.CalcMolFormula(mol),
                                charge=Chem.GetFormalCharge(mol)))
                        st.session_state.candidate_paths=records
                        st.success(f'{len(records)} pH-aware ligand state(s) prepared from {len(mols)} input ligand(s). Review the table before docking.')
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
                            ordered = sorted(range(len(figure_rows)), key=lambda i: figure_rows[i].get('reference_rmsd_A', float('inf')))
                            pose_key = 'validation_figure_pose_' + active_job.name
                            if st.button('Display lowest-RMSD pose', key='best_' + active_job.name):
                                st.session_state[pose_key] = ordered[0]
                            figure_index = st.selectbox('Figure pose · sorted by RMSD', ordered,
                                format_func=lambda i: f"Seed {figure_rows[i]['seed']} · pose {figure_rows[i]['rank']} · RMSD {figure_rows[i].get('reference_rmsd_A', float('nan')):.3f} Å · score {figure_rows[i]['score_kcal_mol']:.2f} kcal/mol", key=pose_key)
                            st.session_state.assistant_active_job = str(active_job)
                            st.session_state.assistant_selected_pose = figure_rows[figure_index]
                            publication_figure(active_job, figure_rows[figure_index])

        else:
            all_jobs=jobs.list_jobs(root)
            if not all_jobs:
                st.info('Run redocking or a docking experiment to see results.')
            else:
                job=st.selectbox('Calculation',all_jobs,format_func=lambda p: p.name[:8]+' · '+jobs.status(p)['state'])
                st.session_state.assistant_active_job = str(job)
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
                    pose_key = 'inspect_pose_' + job.name
                    ordered = sorted(range(len(rows)), key=lambda i: rows[i].get('reference_rmsd_A', rows[i]['score_kcal_mol']))
                    if st.button('Display lowest-RMSD pose' if 'reference_rmsd_A' in df else 'Display lowest-score pose', key='best_explore_' + job.name):
                        st.session_state[pose_key] = ordered[0]
                    index=st.selectbox('Inspect pose',ordered,format_func=lambda i:f"{rows[i]['ligand']} · seed {rows[i]['seed']} · pose {rows[i]['rank']} · score {rows[i]['score_kcal_mol']:.2f}" + (f" · RMSD {rows[i]['reference_rmsd_A']:.3f} Å" if 'reference_rmsd_A' in rows[i] else ''), key=pose_key)
                    row=rows[index]
                    st.session_state.assistant_selected_pose = row
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
                    st.download_button('Download results CSV',df.to_csv(index=False),'results.csv','text/csv', on_click='ignore')
                    st.download_button('Download selected pose SDF',sdf,'selected_pose.sdf', on_click='ignore')
                with st.expander('Saved docking settings'):
                    st.json(config)
            manifest()
            st.download_button('Download complete experiment',core.bundle(root),'pandoc_experiment.zip','application/zip', on_click='ignore')

    except Exception as exc:
        st.session_state.assistant_diagnostic = str(exc)
        st.error(str(exc))
        with st.expander('Diagnostic details'):
            st.exception(exc)

show_assistant()
