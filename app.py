from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from pandoc import core, jobs, figures, pdb_search, structure_checks, phprep, ui, batch_review, profile_engine, compute

PANDOC_LOGO = Path(__file__).parent / 'assets' / 'pandoc_logo.png'
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


def job_path(job, value):
    path = Path(value)
    return path if path.is_absolute() else Path(job) / path


def publication_figure(job, row):
    from rdkit import Chem
    config = json.loads((Path(job)/'config.json').read_text())
    if not config.get('reference'):
        return
    reference_path = job_path(job, config['reference'])
    reference = next(iter(Chem.SDMolSupplier(str(reference_path), removeHs=False)))
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
    if st.button('View structure details', key='view_structure_details'):
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
    if st.button('Load this structure', type='primary', key='load_selected_structure'):
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


def github_backend():
    try:
        token = st.secrets.get('GITHUB_TOKEN', os.environ.get('GITHUB_TOKEN', ''))
        job_key = st.secrets.get('PANDOC_JOB_KEY', os.environ.get('PANDOC_JOB_KEY', ''))
        repository = st.secrets.get('PANDOC_GITHUB_REPOSITORY', compute.DEFAULT_REPOSITORY)
    except Exception:
        token = os.environ.get('GITHUB_TOKEN', '')
        job_key = os.environ.get('PANDOC_JOB_KEY', '')
        repository = os.environ.get('PANDOC_GITHUB_REPOSITORY', compute.DEFAULT_REPOSITORY)
    if not token or not job_key:
        return None
    try:
        return compute.from_credentials(token, job_key, repository)
    except compute.ComputeBackendError:
        return None


def kaggle_config():
    try:
        base_url = st.secrets.get('PANDOC_KAGGLE_URL', os.environ.get('PANDOC_KAGGLE_URL', ''))
        api_key = st.secrets.get('PANDOC_KAGGLE_API_KEY', os.environ.get('PANDOC_KAGGLE_API_KEY', ''))
    except Exception:
        base_url = os.environ.get('PANDOC_KAGGLE_URL', '')
        api_key = os.environ.get('PANDOC_KAGGLE_API_KEY', '')
    return (base_url or '').strip(), (api_key or '').strip()


def kaggle_backend():
    base_url, api_key = kaggle_config()
    if not base_url or not api_key:
        return None
    try:
        return compute.kaggle_from_credentials(base_url, api_key)
    except Exception:
        return None


def kaggle_config_problem():
    base_url, api_key = kaggle_config()
    missing = []
    if not base_url:
        missing.append('PANDOC_KAGGLE_URL')
    if not api_key:
        missing.append('PANDOC_KAGGLE_API_KEY')
    if missing:
        return 'Streamlit cannot see: ' + ', '.join(missing)
    if not base_url.startswith(('https://', 'http://')):
        return 'PANDOC_KAGGLE_URL is not a valid http(s) URL.'
    return ''


def backend_for_handle(handle):
    if handle and handle.get('backend') == 'kaggle-direct':
        return kaggle_backend()
    return github_backend()


def prepare_candidates_on_streamlit(ligands, ph, enumerate_states):
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors

    prepared = []
    for parent_index, item in enumerate(ligands, 1):
        name = item['name']
        smiles = item['smiles']
        if enumerate_states:
            states = phprep.enumerate_ligand_states(smiles, ph)
            for state in states:
                prepared.append((name, parent_index, state['index'], state['mol'], state['smiles']))
        else:
            mol = core.molecule(smiles=smiles)
            state_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
            prepared.append((name, parent_index, 1, mol, state_smiles))

    if len(prepared) > 25:
        raise ValueError(
            f'pH-aware enumeration generated {len(prepared)} states. '
            'Reduce the input set or prepare compounds in smaller batches (maximum 25 states per run).'
        )

    directory = root/'candidates'/uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for i, (name, parent_index, state_index, mol, state_smiles) in enumerate(prepared, 1):
        ident = f'ligand_{i:03d}'
        path = directory/(ident+'.pdbqt')
        core.write_ligand(mol, path)
        records.append(dict(
            id=ident, name=name, parent=parent_index, microstate=state_index,
            smiles=state_smiles, pH=ph, path=str(path),
            formula=rdMolDescriptors.CalcMolFormula(mol),
            charge=int(Chem.GetFormalCharge(mol)),
        ))
    return records


def cloud_compute_mode():
    mode = st.session_state.get('compute_mode', 'Computer A · Fast')
    return mode.startswith('Computer A') or mode.startswith('Streamlit Cloud')


def kaggle_compute_mode():
    mode = st.session_state.get('compute_mode', '')
    return mode.startswith('Computer B') or mode.startswith('Kaggle')


def show_compute_progress(backend, remote):
    try:
        progress = backend.progress(remote)
    except compute.ComputeBackendError as exc:
        st.warning(str(exc))
        return

    steps = progress.get('steps', [])
    if not steps:
        st.caption('Waiting for compute runner…')
        return

    marks = {
        'success': '✓',
        'failure': '✕',
        'cancelled': '■',
        'skipped': '–',
    }
    lines = []
    for step in steps:
        status = step.get('status')
        conclusion = step.get('conclusion')
        if status == 'in_progress':
            mark = '●'
        elif status == 'queued':
            mark = '○'
        else:
            mark = marks.get(conclusion, '○')
        lines.append(f"{mark} {step.get('name', 'Compute step')}")
    st.code('\n'.join(lines), language=None)


def show_microstate_job(task_key, result_key):
    remote = st.session_state.get(task_key)
    if not remote:
        return
    backend = backend_for_handle(remote)
    if backend is None:
        node = 'Computer B' if remote.get('backend') == 'kaggle-direct' else 'Computer C'
        st.warning(node + ' is not available for ligand pH enumeration.')
        return

    try:
        initial = backend.status(remote)
    except compute.ComputeBackendError as exc:
        st.warning(str(exc))
        return
    polling = initial.get('state') in ('queued', 'running', 'starting')

    @st.fragment(run_every=5 if polling else None)
    def live_microstate_job():
        try:
            state = backend.status(remote)
        except compute.ComputeBackendError as exc:
            st.warning(str(exc))
            return

        ui.automation_marker(st, 'ligand-state-job', state=state['state'], value=remote['job_id'])
        st.info(f"Ligand-state enumeration · {state['state']} · {remote['job_id'][:8]}")
        with st.expander('Compute progress', expanded=True):
            show_compute_progress(backend, remote)

        if state.get('state') == 'completed':
            target = root/'remote_tasks'/remote['job_id']
            try:
                if not (target/'results.json').exists():
                    backend.materialize(remote, target)
                payload = json.loads((target/'results.json').read_text())
                st.session_state[result_key] = payload.get('states', [])
                backend.cleanup(remote)
                st.session_state.pop(task_key, None)
                st.rerun()
            except (OSError, json.JSONDecodeError, compute.ComputeBackendError) as exc:
                st.warning(str(exc))
        elif state.get('state') == 'failed':
            st.error(state.get('error') or 'Ligand-state enumeration failed on the selected compute node.')
        elif state.get('state') == 'cancelled':
            st.warning('Ligand-state enumeration was cancelled.')

    live_microstate_job()


def show_candidate_prep_job(task_key):
    remote = st.session_state.get(task_key)
    if not remote:
        return
    backend = backend_for_handle(remote)
    if backend is None:
        node = 'Computer B' if remote.get('backend') == 'kaggle-direct' else 'Computer C'
        st.warning(node + ' is not available for candidate preparation.')
        return

    try:
        initial = backend.status(remote)
    except compute.ComputeBackendError as exc:
        st.warning(str(exc))
        return
    polling = initial.get('state') in ('queued', 'running', 'starting')

    @st.fragment(run_every=5 if polling else None)
    def live_candidate_job():
        try:
            state = backend.status(remote)
        except compute.ComputeBackendError as exc:
            st.warning(str(exc))
            return

        ui.automation_marker(st, 'candidate-preparation-job', state=state['state'], value=remote['job_id'])
        st.info(f"Candidate preparation · {state['state']} · {remote['job_id'][:8]}")
        with st.expander('Compute progress', expanded=True):
            show_compute_progress(backend, remote)

        if state.get('state') == 'completed':
            target = root/'candidates'/('remote_'+remote['job_id'])
            try:
                if not (target/'results.json').exists():
                    backend.materialize(remote, target)
                payload = json.loads((target/'results.json').read_text())
                records = payload.get('records', [])
                for record in records:
                    record['path'] = str((target/record['path']).resolve())
                st.session_state.candidate_paths = records
                backend.cleanup(remote)
                st.session_state.pop(task_key, None)
                st.rerun()
            except (OSError, json.JSONDecodeError, compute.ComputeBackendError) as exc:
                st.warning(str(exc))
        elif state.get('state') == 'failed':
            st.error(state.get('error') or 'Candidate preparation failed on the selected compute node.')
        elif state.get('state') == 'cancelled':
            st.warning('Candidate preparation was cancelled.')

    live_candidate_job()



def _merge_remote_profile_result(target_name, materialized_root, result):
    profile_root = root/'reviewed_profile_preparation'
    profile_root.mkdir(parents=True, exist_ok=True)

    source_dir = Path(materialized_root)/target_name
    if not source_dir.is_dir():
        raise OSError(f'Remote profile artifact is missing the {target_name} output directory.')

    destination = profile_root/target_name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_dir, destination)

    # Replace runner-local paths with the materialized PanDoc-session path.
    receptor_candidates = list(destination.rglob('receptor_curated_heme.pdbqt'))
    if receptor_candidates:
        result = dict(result)
        result['receptor_pdbqt'] = str(receptor_candidates[0].resolve())

    current = st.session_state.get('profile_prepare_result') or {
        'results': [], 'errors': [], 'bundle': b''
    }
    results = [
        item for item in current.get('results', [])
        if item.get('target') != target_name
    ]
    errors = [
        item for item in current.get('errors', [])
        if item.get('target') != target_name
    ]
    results.append(result)
    st.session_state.profile_prepare_result = {
        'results': results,
        'errors': errors,
        'bundle': core.bundle(profile_root),
    }


def show_profile_prep_jobs():
    remotes = st.session_state.get('profile_remote_jobs') or {}
    if not remotes:
        return

    active_states = ('queued', 'running', 'starting')
    states = {}
    for target_name, remote in remotes.items():
        backend = backend_for_handle(remote)
        if backend is None:
            states[target_name] = {'state': 'failed', 'error': 'Computer C is not configured.'}
            continue
        try:
            states[target_name] = backend.status(remote)
        except compute.ComputeBackendError as exc:
            states[target_name] = {'state': 'failed', 'error': str(exc)}

    polling = any(item.get('state') in active_states for item in states.values())

    @st.fragment(run_every=5 if polling else None)
    def live_profile_prep_jobs():
        current_jobs = dict(st.session_state.get('profile_remote_jobs') or {})
        changed = False

        for target_name, remote in current_jobs.items():
            backend = backend_for_handle(remote)
            if backend is None:
                st.error(f'{target_name} · Computer C is not configured.')
                continue

            try:
                state = backend.status(remote)
            except compute.ComputeBackendError as exc:
                st.error(f'{target_name} · {exc}')
                continue

            ui.automation_marker(
                st,
                'profile-preparation-job',
                state=state.get('state', 'starting'),
                value=remote['job_id'],
                text=f"{target_name} profile preparation {state.get('state', 'starting')}",
            )
            st.info(
                f"{target_name} curated receptor preparation · "
                f"{state.get('state', 'starting')} · {remote['job_id'][:8]}"
            )
            with st.expander(f'{target_name} compute progress', expanded=True):
                show_compute_progress(backend, remote)
                try:
                    st.code(backend.logs(remote, tail=120) or 'Waiting for compute output.', language=None)
                except Exception:
                    pass

            if state.get('state') == 'completed':
                materialized = root/'remote_profile_tasks'/remote['job_id']
                try:
                    if not (materialized/'results.json').exists():
                        backend.materialize(remote, materialized)
                    payload = json.loads((materialized/'results.json').read_text())
                    result = payload.get('result')
                    if not isinstance(result, dict):
                        raise ValueError('Remote profile preparation returned no result record.')
                    _merge_remote_profile_result(target_name, materialized, result)
                    backend.cleanup(remote)
                    current_jobs.pop(target_name, None)
                    st.session_state.profile_remote_jobs = current_jobs
                    changed = True
                except (OSError, ValueError, json.JSONDecodeError, compute.ComputeBackendError) as exc:
                    st.error(f'{target_name} result retrieval failed: {exc}')

            elif state.get('state') in ('failed', 'cancelled'):
                message = state.get('error') or (
                    'Curated receptor preparation was cancelled.'
                    if state.get('state') == 'cancelled'
                    else 'Curated receptor preparation failed on Computer C.'
                )
                current = st.session_state.get('profile_prepare_result') or {
                    'results': [], 'errors': [], 'bundle': b''
                }
                errors = [
                    item for item in current.get('errors', [])
                    if item.get('target') != target_name
                ]
                errors.append({'target': target_name, 'error': message})
                current['errors'] = errors
                st.session_state.profile_prepare_result = current
                current_jobs.pop(target_name, None)
                st.session_state.profile_remote_jobs = current_jobs
                changed = True

        if changed:
            profile_root = root/'reviewed_profile_preparation'
            current = st.session_state.get('profile_prepare_result')
            if current is not None and profile_root.exists():
                current['bundle'] = core.bundle(profile_root)
            st.rerun()

    live_profile_prep_jobs()


def show_remote_job(remote, local_key, remote_key):
    backend = backend_for_handle(remote)
    if backend is None:
        label = 'Computer B' if remote.get('backend') == 'kaggle-direct' else 'Computer C'
        st.error(label + ' is not configured or reachable.')
        return {'state': 'failed'}
    try:
        state = backend.status(remote)
    except compute.ComputeBackendError as exc:
        st.error(str(exc))
        return {'state': 'failed'}

    active_states = ('queued', 'running', 'starting')
    polling = state['state'] in active_states

    @st.fragment(run_every=2 if polling else None)
    def live_remote_job():
        try:
            current = backend.status(remote)
        except compute.ComputeBackendError as exc:
            st.error(str(exc))
            return
        backend_label = 'Computer B' if remote.get('backend') == 'kaggle-direct' else 'Computer C'
        ui.automation_marker(
            st,
            'job-status',
            state=current['state'],
            value=remote['job_id'],
            text=f"{backend_label} {current['state']}",
        )
        st.info(f"{backend_label} job: {current['state']} · {remote['job_id'][:8]}")
        with st.expander('Compute progress', expanded=True):
            show_compute_progress(backend, remote)

        # Surface the worker output directly in PanDoc so the user can watch
        # Vina/worker progress without opening Kaggle or GitHub.
        try:
            live_log = backend.logs(remote, tail=160)
        except Exception as exc:
            live_log = f'Waiting for calculation log…\n{exc}'
        with st.expander('Live calculation log', expanded=True):
            st.code(live_log or 'Waiting for worker.', language=None)

        if current['state'] in active_states:
            if remote.get('backend') == 'kaggle-direct':
                st.caption('Running on Computer B. Status and log refresh every 2 seconds.')
            else:
                st.caption('Running on Computer C. Status and log refresh every 2 seconds.')
            cancel_key = remote['job_id'] + '_cancel_confirm'
            if st.session_state.get(cancel_key):
                st.warning('Cancel this calculation? Current progress for the active search may be lost.')
                a, b = st.columns(2)
                if a.button('Yes, cancel calculation', key=remote['job_id']+'_cancel_yes'):
                    backend.cancel(remote)
                    st.session_state.pop(cancel_key, None)
                    st.info('Cancellation requested.')
                if b.button('Keep running', key=remote['job_id']+'_cancel_no'):
                    st.session_state.pop(cancel_key, None)
                    st.rerun()
            elif st.button('Cancel calculation', key=remote['job_id']+'cancel'):
                st.session_state[cancel_key] = True
                st.rerun()
        elif current['state'] == 'completed':
            target = root/'jobs'/('github_'+remote['job_id'])
            try:
                if not (target/'status.json').exists():
                    with st.spinner('Retrieving docking results...'):
                        backend.materialize(remote, target)
                        backend.cleanup(remote)
                st.session_state[local_key] = str(target)
                st.session_state.pop(remote_key, None)
                st.rerun()
            except compute.ComputeBackendError as exc:
                st.error(str(exc))
        elif current['state'] == 'failed':
            st.error(current.get('error') or 'Docking failed on the selected compute node.')
        elif current['state'] == 'cancelled':
            st.warning('Docking job cancelled.')

    live_remote_job()
    return state


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
        ui.automation_marker(
            st,
            'job-status',
            state=state['state'],
            value=Path(directory).name,
            text=f"Computer A {state['state']}",
        )
        st.info(f"Job: {state['state']} · {Path(directory).name[:8]}")
        if state.get('error'):
            st.error(state['error'])
        total = state.get('total', 0)
        if total:
            completed = state.get('completed', 0)
            st.progress(min(completed / total, 1.0), text=f'{completed}/{total} docking searches completed')
        if state['state'] in active_states:
            st.caption('Updates automatically every 2 seconds. Cancellation takes effect between docking searches.')
            cancel_key = str(directory) + '_cancel_confirm'
            if st.session_state.get(cancel_key):
                st.warning('Cancel this calculation? Current progress for the active search may be lost.')
                a, b = st.columns(2)
                if a.button('Yes, cancel calculation', key=str(directory)+'_cancel_yes'):
                    jobs.cancel(directory)
                    st.session_state.pop(cancel_key, None)
                    st.info('Cancellation requested.')
                if b.button('Keep running', key=str(directory)+'_cancel_no'):
                    st.session_state.pop(cancel_key, None)
                    st.rerun()
            elif st.button('Cancel calculation', key=str(directory)+'cancel'):
                st.session_state[cancel_key] = True
                st.rerun()
        log = Path(directory)/'worker.log'
        with st.expander('Calculation log', expanded=polling):
            st.code(log.read_text(errors='replace')[-16000:] if log.exists() else 'Waiting for worker.')
    live_job()
    return initial


if st.session_state.get('next_stage'):
    st.session_state.workflow_stage = st.session_state.pop('next_stage')

with st.sidebar:
    if PANDOC_LOGO.exists():
        st.image(str(PANDOC_LOGO), width=250)
    else:
        st.markdown(
            '<div style="font-size:1.7rem;font-weight:850;color:#0f2a43;letter-spacing:-.03em">Pan<span style="color:#0f8f91">Doc</span></div>',
            unsafe_allow_html=True,
        )
    st.text_input('Experiment name', 'My docking experiment', key='experiment')
    stage = st.radio('Workflow', ['1 · Load complex', '2 · Prepare structures', '3 · Validate docking', '4 · Run experiment', '5 · Explore results'], key='workflow_stage')
    st.radio(
        'Compute',
        ['Computer A · Fast', 'Computer B · High capacity', 'Computer C · Backup'],
        key='compute_mode',
        help='Choose the compute node for this calculation. Computer A is the fast default, Computer B is the high-capacity node, and Computer C is the backup node.',
    )
    if kaggle_compute_mode():
        _k_url, _k_key = kaggle_config()
        if _k_url and _k_key:
            st.caption('Computer B · Connected ✓')
        else:
            st.warning('Computer B is not configured for this session.')
    st.toggle('Assistant', key='assistant_open', value=False)
    st.divider()
    st.caption('✓ Complex loaded' if st.session_state.get('pdb') else '○ Load a complex')
    st.caption('✓ Receptor prepared' if st.session_state.get('preparation_id') else '○ Prepare receptor')
    st.caption('✓ Reference prepared' if st.session_state.get('reference_path') else '○ Prepare reference')
    st.markdown('<div class="pd-section-label">Scientific units</div>', unsafe_allow_html=True)
    st.caption('Coordinates in Å · Vina scores in kcal/mol')
    if st.button('Start a new experiment', key='start_new_experiment'):
        st.session_state.clear()
        st.rerun()

    st.divider()
    st.markdown('<div class="pd-section-label">Batch receptor QC</div>', unsafe_allow_html=True)
    st.caption('Server-side review of FYN 10DJ, AR 2AMA, CYP19A1 3S79 and PGR 1A28. No Playwright or local installation.')
    if st.button('Run 4-receptor batch review', type='primary'):
        try:
            with st.spinner('Running structure checks, heavy-atom repair and PROPKA for four receptors…'):
                st.session_state.batch_review_result = batch_review.run_batch(root/'batch_review')
            st.success('Four-receptor batch review completed.')
        except Exception as exc:
            st.session_state.pop('batch_review_result', None)
            st.error('Batch review failed: '+str(exc))
    batch_result = st.session_state.get('batch_review_result')
    if batch_result:
        st.download_button(
            'Download 4-receptor review ZIP',
            batch_result['bundle'],
            'PanDoc_4Receptors_Review_Outputs.zip',
            'application/zip',
        )

    st.divider()
    st.markdown('<div class="pd-section-label">Reviewed receptor profiles</div>', unsafe_allow_html=True)
    profile_targets = list(profile_engine.load_profiles()[0])
    selected_profiles = st.multiselect(
        'Targets to prepare',
        profile_targets,
        default=profile_targets,
        key='reviewed_profile_targets',
    )
    st.caption(
        'Applies reviewed pH 7.4 residue states. Standard profiles prepare in the '
        'Streamlit session; curated heme profiles are sent to Computer C, where '
        'AutoDockTools 1.5.7 is available specifically for heme-preserving receptor preparation.'
    )
    profile_jobs_active = bool(st.session_state.get('profile_remote_jobs'))
    if st.button(
        'Prepare reviewed profiles',
        type='primary',
        disabled=not selected_profiles or profile_jobs_active,
    ):
        try:
            targets_cfg, _profile_meta = profile_engine.load_profiles()
            standard_profiles = [
                name for name in selected_profiles
                if targets_cfg[name].get('preparation_mode', 'standard') != 'curated_heme'
            ]
            curated_profiles = [
                name for name in selected_profiles
                if targets_cfg[name].get('preparation_mode', 'standard') == 'curated_heme'
            ]

            profile_root = root/'reviewed_profile_preparation'
            if profile_root.exists():
                shutil.rmtree(profile_root)
            profile_root.mkdir(parents=True, exist_ok=True)

            if standard_profiles:
                with st.spinner('Preparing standard reviewed receptor profiles…'):
                    local_result = profile_engine.prepare_many(
                        standard_profiles,
                        profile_root,
                    )
            else:
                local_result = {'results': [], 'errors': [], 'bundle': core.bundle(profile_root)}

            st.session_state.profile_prepare_result = local_result
            st.session_state.pop('profile_remote_jobs', None)

            if curated_profiles:
                github = github_backend()
                if github is None:
                    errors = list(local_result.get('errors', []))
                    for target_name in curated_profiles:
                        errors.append({
                            'target': target_name,
                            'error': (
                                'Curated heme preparation requires Computer C. '
                                'Configure GITHUB_TOKEN and PANDOC_JOB_KEY.'
                            ),
                        })
                    local_result['errors'] = errors
                    st.session_state.profile_prepare_result = local_result
                else:
                    remote_jobs = {}
                    for target_name in curated_profiles:
                        remote_jobs[target_name] = github.submit_profile_preparation(target_name)
                    st.session_state.profile_remote_jobs = remote_jobs
                    st.info(
                        'Curated heme profile preparation submitted to Computer C. '
                        'PanDoc will retrieve and merge the completed receptor automatically.'
                    )

            _profile_result = st.session_state.profile_prepare_result
            if _profile_result.get('errors'):
                st.warning(
                    f"Reviewed-profile preparation currently has "
                    f"{len(_profile_result['errors'])} failed target(s)."
                )
            elif not curated_profiles:
                st.success('Reviewed-profile preparation finished for all selected targets.')
        except Exception as exc:
            st.session_state.pop('profile_remote_jobs', None)
            st.error('Reviewed-profile preparation failed: '+str(exc))

    show_profile_prep_jobs()

    profile_result = st.session_state.get('profile_prepare_result')
    if profile_result:
        st.download_button(
            'Download prepared-profile bundle',
            profile_result['bundle'],
            'PanDoc_Reviewed_Profile_Preparation.zip',
            'application/zip',
            key='profile_bundle_download_sidebar',
        )

ui.automation_snapshot(
    st,
    stage=stage,
    compute_node=st.session_state.get('compute_mode', 'Computer A · Fast'),
    complex_loaded=bool(st.session_state.get('pdb')),
    receptor_ready=bool(st.session_state.get('preparation_id')),
    reference_ready=bool(st.session_state.get('reference_path')),
)

from pandoc import assistant

if st.session_state.get('assistant_open', False):
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
    ui.workflow_stepper(st, stage)

    profile_result = st.session_state.get('profile_prepare_result')
    if profile_result:
        with st.expander('Reviewed receptor profile preparation', expanded=True):
            if profile_result['results']:
                st.dataframe(pd.DataFrame(profile_result['results']), hide_index=True, width='stretch')
            if profile_result['errors']:
                st.error('One or more reviewed profiles failed.')
                st.dataframe(pd.DataFrame(profile_result['errors']), hide_index=True, width='stretch')
                for item in profile_result['errors']:
                    st.markdown(f"**{item.get('target', 'Unknown target')} failed**")
                    st.code(str(item.get('error', 'No diagnostic message returned.')), language=None)
            elif st.session_state.get('profile_remote_jobs'):
                st.info('Curated receptor preparation is still running on Computer C.')
            else:
                st.success('All selected reviewed receptor profiles prepared successfully.')
            st.caption(
                'Standard receptors use reviewed residue states. CYP19A1 is now routed through '
                'the dedicated curated heme branch, which retains HEM and verifies Fe–Cys437 '
                'coordination before accepting the receptor.'
            )
            st.download_button(
                'Download reviewed-profile bundle',
                profile_result['bundle'],
                'PanDoc_Reviewed_Profile_Preparation.zip',
                'application/zip',
                key='profile_bundle_download_main',
            )

    batch_result = st.session_state.get('batch_review_result')
    if batch_result:
        with st.expander('4-receptor batch review', expanded=True):
            if batch_result['summary']:
                st.dataframe(pd.DataFrame(batch_result['summary']), hide_index=True, width='stretch')
            if batch_result['errors']:
                st.warning('Some receptors need attention before review can be considered complete.')
                st.dataframe(pd.DataFrame(batch_result['errors']), hide_index=True, width='stretch')
            st.caption('Each target folder includes structure issues before/after repair, repair changes, full PROPKA review, residues requiring review, histidines and candidate crystallographic waters within 4 Å of the reference ligand.')
            st.download_button(
                'Download complete batch review',
                batch_result['bundle'],
                'PanDoc_4Receptors_Review_Outputs.zip',
                'application/zip',
                key='batch_review_download_main',
            )
    ui.status_grid(st,
        complex_loaded=bool(st.session_state.get('pdb')),
        receptor_ready=bool(st.session_state.get('preparation_id')),
        reference_ready=bool(st.session_state.get('reference_path')))
    try:
        if stage.startswith('1'):
            st.write('Upload a complex, inspect its components and select the receptor and crystallographic reference ligand.')
            source_mode = st.radio('Structure source', ['Upload file', 'Search PDB'], horizontal=True, key='structure_source_mode')
            if source_mode == 'Upload file':
                upload = st.file_uploader('PDB or mmCIF complex', type=['pdb', 'cif', 'mmcif'], key='complex_upload')
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
                    # A retained heme must not fall through Meeko's failing CCD path.
                    # This mode specifically requires reviewed P450-like SG--Fe coordination.
                    heme_atoms = [a for a in core.atoms(selected_input)
                                  if a['res'].upper() == 'HEM']
                    heme_coordination_residue = None
                    if heme_atoms:
                        from math import dist
                        irons = [a for a in heme_atoms if a['element'].upper() == 'FE']
                        cysteine_sulfurs = [a for a in core.atoms(selected_input)
                                             if a['res'].upper() == 'CYS'
                                             and a['name'].upper() == 'SG']
                        with st.expander('Retained HEM · curated heme preparation', expanded=True):
                            st.warning(
                                'HEM has coordinated iron. PanDoc does not use Meeko CCD '
                                'auto-templates or silently remove HEM. The existing '
                                'curated AutoDockTools backend is required.'
                            )
                            if len(irons) != 1:
                                st.error('HEM preparation requires exactly one reviewed Fe center.')
                            elif not cysteine_sulfurs:
                                st.error(
                                    'No coordinating cysteine SG was found. This curated '
                                    'P450-like pathway is not suitable for a different heme '
                                    'coordination type. Supply a validated receptor instead.'
                                )
                            else:
                                candidates = sorted(
                                    [(f"{a['chain'] or '_'}:{a['number']}{a['icode']}",
                                      dist(a['xyz'], irons[0]['xyz']))
                                     for a in cysteine_sulfurs],
                                    key=lambda item: item[1]
                                )
                                labels = ['Select reviewed proximal CYS (required)'] + [
                                    f'{ident} · SG--Fe {distance:.2f} Å'
                                    for ident, distance in candidates
                                ]
                                choice = st.selectbox(
                                    'Fe-coordinating cysteine (review crystallographic geometry)',
                                    labels, key='heme_cysteine_'+core.digest(selected_input)
                                )
                                if choice != labels[0]:
                                    index = labels.index(choice) - 1
                                    heme_coordination_residue = candidates[index][0]
                                    if candidates[index][1] > 3.0:
                                        st.error(
                                            'Selected SG--Fe distance is >3 Å; this site '
                                            'will fail curated geometry validation.'
                                        )
                                        heme_coordination_residue = None
                                st.caption(
                                    'This path verifies Fe retention and SG deprotonation. '
                                    'On Streamlit Cloud, use the Computer C curated-heme '
                                    'profile if AutoDockTools is not available locally.'
                                )
                    ph_prediction_key = core.digest(selected_input, repair, intended_ph)
                    if st.button('Analyze protonation at selected pH', key='analyze_protonation'):
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
                    if st.button('Prepare receptor', type='primary', disabled=(not bool(reviewed_templates) or (bool(heme_atoms) and not heme_coordination_residue)), key='prepare_receptor'):
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
                        prep_id = core.digest(final, combined_templates, intended_ph, heme_coordination_residue, st.session_state.get('protonation_review'), core.versions())
                        directory = root/'preparations'/prep_id
                        with st.spinner('Checking chemistry and preparing receptor…'):
                            selection_path = root/'selection_report.json'
                            report = dict(selection_report=json.loads(selection_path.read_text()) if selection_path.exists() else {}, input_checks=checks, repaired_checks=final_checks, repair_changes=repair_changes, settings=dict(pH=intended_ph, templates=combined_templates, rationale=notes, curated_input=bool(curated), heme_coordination_residue=heme_coordination_residue, protonation_review=st.session_state.get('protonation_review')), software_versions=core.versions())
                            directory.mkdir(parents=True, exist_ok=True)
                            (directory/'structure_report.json').write_text(json.dumps(report, indent=2))
                            try:
                                path = core.prepare_receptor(final, directory, combined_templates,
                                                             heme_coordination_residue=heme_coordination_residue,
                                                             heme_source_pdb=selected_input)
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
                            if (directory/'curated_heme_audit.json').exists():
                                report['curated_heme_audit'] = json.loads((directory/'curated_heme_audit.json').read_text())
                            if (directory/'cofactor_resolution.json').exists():
                                report['cofactor_resolution'] = json.loads((directory/'cofactor_resolution.json').read_text())
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
                            preparation_record=dict(pH=intended_ph, templates=combined_templates, heme_coordination_residue=heme_coordination_residue, protonation_review=st.session_state.get('protonation_review'), repaired_heavy_atoms=repair, curated_upload=curated.name if curated else None, rationale=notes))
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
                        task_key = 'reference_microstates_job_'+identity+'_'+str(ligand_ph)
                        microstate_busy = bool(st.session_state.get(task_key))
                        if st.button(f'Enumerate ligand states at pH {ligand_ph:.1f}', disabled=microstate_busy):
                            if not smiles.strip():
                                st.warning('Enter or retrieve the ligand SMILES first.')
                            elif cloud_compute_mode():
                                try:
                                    with st.spinner('Enumerating ligand states on Streamlit Cloud…'):
                                        states = phprep.enumerate_ligand_states(smiles, ligand_ph, 16)
                                    st.session_state[state_key] = [
                                        dict(index=s['index'], smiles=s['smiles'], formal_charge=s['formal_charge'])
                                        for s in states
                                    ]
                                    st.success('Completed on Streamlit Cloud.')
                                except (RuntimeError, MemoryError, OSError) as exc:
                                    backend = github_backend()
                                    if backend is None:
                                        st.warning(f'Computer A failed and Computer C fallback is unavailable: {exc}')
                                    else:
                                        st.warning('Streamlit Cloud compute was unavailable. Switching to Computer C…')
                                        st.session_state[task_key] = backend.submit_ligand_microstates(smiles, ligand_ph, 16)
                                        st.rerun()
                            else:
                                backend = kaggle_backend() if kaggle_compute_mode() else github_backend()
                                if backend is None:
                                    st.warning(('Computer B' if kaggle_compute_mode() else 'Computer C') + ' is not configured.')
                                else:
                                    try:
                                        st.session_state[task_key] = backend.submit_ligand_microstates(smiles, ligand_ph, 16)
                                        st.rerun()
                                    except Exception as exc:
                                        if kaggle_compute_mode():
                                            fallback = github_backend()
                                            if fallback is not None:
                                                st.warning('Computer B could not start ligand-state enumeration. Switching to Computer C…')
                                                st.session_state[task_key] = fallback.submit_ligand_microstates(smiles, ligand_ph, 16)
                                                st.rerun()
                                            else:
                                                st.warning(str(exc))
                                        else:
                                            st.warning(str(exc))
                        show_microstate_job(task_key, state_key)
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
                        if st.button('Prepare reference ligand',disabled=not comparison['valid'] or not reviewed, key='prepare_reference_ligand'):
                            try:
                                mol = core.reference_from_pdb(ref, selected_smiles)
                                ident = core.digest(ref, selected_smiles.strip(), ligand_ph)
                                directory = root/'references'/ident
                                directory.mkdir(parents=True, exist_ok=True)
                                core.write_ligand(mol, directory/'reference.pdbqt')
                                source=definition if definition and definition['smiles']==selected_smiles.strip() else {'source':'Molscrub pH-aware state' if selected_state else 'Manual SMILES','smiles':selected_smiles.strip(),'pH':ligand_ph,'formal_charge':selected_state['formal_charge'] if selected_state else Chem.GetFormalCharge(mol)}
                                st.session_state.update(reference_path=str(directory/'reference.sdf'), reference_pdbqt=str(directory/'reference.pdbqt'), reference_smiles=selected_smiles.strip(), reference_id=ident,reference_chemistry_source=source)
                                manifest()
                                ui.automation_marker(st, 'reference-prepared', state='completed', value=ident)
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
                    candidate_task_key = 'candidate_preparation_job'
                    candidate_busy = bool(st.session_state.get(candidate_task_key))
                    if st.button('Prepare candidate ligands', disabled=not chemical_review or candidate_busy, key='prepare_candidate_ligands'):
                        from rdkit import Chem
                        ligand_inputs = []
                        if upload:
                            import io
                            for i, mol in enumerate(Chem.ForwardSDMolSupplier(io.BytesIO(upload.getvalue()), removeHs=False)):
                                if mol is None:
                                    raise ValueError(f'Invalid molecule in SDF record {i+1}.')
                                name = mol.GetProp('_Name') if mol.HasProp('_Name') and mol.GetProp('_Name').strip() else f'Ligand {i+1}'
                                state_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)
                                ligand_inputs.append({'name': name, 'smiles': state_smiles})
                        for line in smiles_text.splitlines():
                            if line.strip():
                                pieces = line.split(maxsplit=1)
                                ligand_inputs.append({
                                    'name': pieces[1] if len(pieces) > 1 else f'Ligand {len(ligand_inputs)+1}',
                                    'smiles': pieces[0],
                                })
                        if not 1 <= len(ligand_inputs) <= 25:
                            raise ValueError('Prepare between one and 25 input candidate ligands.')
                        if cloud_compute_mode():
                            try:
                                with st.spinner('Preparing candidate ligands on Streamlit Cloud…'):
                                    st.session_state.candidate_paths = prepare_candidates_on_streamlit(
                                        ligand_inputs, candidate_ph, enumerate_states
                                    )
                                st.success('Candidate preparation completed on Streamlit Cloud.')
                            except (RuntimeError, MemoryError, OSError) as exc:
                                backend = github_backend()
                                if backend is None:
                                    st.warning(f'Streamlit Cloud preparation failed and GitHub fallback is unavailable: {exc}')
                                else:
                                    st.warning('Streamlit Cloud resources were unavailable. Switching preparation to GitHub Actions…')
                                    st.session_state[candidate_task_key] = backend.submit_candidate_preparation(
                                        ligand_inputs, candidate_ph, enumerate_states
                                    )
                                    st.rerun()
                        else:
                            backend = kaggle_backend() if kaggle_compute_mode() else github_backend()
                            if backend is None:
                                st.warning(('Computer B' if kaggle_compute_mode() else 'Computer C') + ' is not configured.')
                            else:
                                try:
                                    st.session_state[candidate_task_key] = backend.submit_candidate_preparation(
                                        ligand_inputs, candidate_ph, enumerate_states
                                    )
                                    st.rerun()
                                except Exception as exc:
                                    if kaggle_compute_mode():
                                        fallback = github_backend()
                                        if fallback is not None:
                                            st.warning('Computer B could not start candidate preparation. Switching to Computer C…')
                                            st.session_state[candidate_task_key] = fallback.submit_candidate_preparation(
                                                ligand_inputs, candidate_ph, enumerate_states
                                            )
                                            st.rerun()
                                        else:
                                            st.warning(str(exc))
                                    else:
                                        st.warning(str(exc))
                    show_candidate_prep_job(candidate_task_key)
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
                remote = st.session_state.get(prefix+'_github_job')
                active = st.session_state.get(prefix+'_job')
                remote_state = None
                github = github_backend()
                kaggle = kaggle_backend() if kaggle_compute_mode() or (remote and remote.get('backend') == 'kaggle-direct') else None
                backend = backend_for_handle(remote) if remote else (kaggle if kaggle_compute_mode() else github)
                if remote and backend:
                    try:
                        remote_state = backend.status(remote)
                    except Exception:
                        remote_state = {'state': 'failed'}
                remote_busy = remote_state and remote_state.get('state') in ('queued','running','starting')
                cloud_busy = bool(active and jobs.status(active).get('state') in ('queued','running','starting'))
                busy = bool(remote_busy or cloud_busy)

                if cloud_compute_mode():
                    st.caption('Compute · Computer A · Computer C fallback enabled' if github else 'Compute · Computer A')
                elif kaggle_compute_mode():
                    st.caption('Compute · Computer B · Computer C fallback enabled' if github else 'Compute · Computer B')
                else:
                    st.caption('Compute · Computer C')

                if st.button('Run redocking' if validation else 'Run docking', type='primary', disabled=busy or not config['ligands'], key=prefix+'_run_calculation'):
                    manifest()
                    if cloud_compute_mode():
                        try:
                            directory = jobs.launch(root, config)
                            st.session_state[prefix+'_job'] = directory
                            st.session_state.pop(prefix+'_github_job', None)
                            st.rerun()
                        except (RuntimeError, MemoryError, OSError, subprocess.SubprocessError) as exc:
                            if github is None:
                                st.error(f'Computer A failed and Computer C fallback is unavailable: {exc}')
                            else:
                                st.warning('Computer A could not start. Switching to Computer C…')
                                try:
                                    remote_job = github.submit(root, config)
                                    st.session_state[prefix+'_github_job'] = remote_job
                                    st.session_state.pop(prefix+'_job', None)
                                    st.rerun()
                                except compute.ComputeBackendError as remote_exc:
                                    st.error(str(remote_exc))
                    elif kaggle_compute_mode():
                        if kaggle is None:
                            problem = kaggle_config_problem() or 'Computer B could not be configured.'
                            if github is None:
                                st.error(problem + ' Computer C fallback is not configured.')
                            else:
                                st.warning(problem + ' Switching to Computer C…')
                                try:
                                    remote_job = github.submit(root, config)
                                    st.session_state[prefix+'_github_job'] = remote_job
                                    st.session_state.pop(prefix+'_job', None)
                                    st.rerun()
                                except compute.ComputeBackendError as exc:
                                    st.error(str(exc))
                        else:
                            try:
                                remote_job = kaggle.submit(root, config)
                                st.session_state[prefix+'_github_job'] = remote_job
                                st.session_state.pop(prefix+'_job', None)
                                st.rerun()
                            except Exception as exc:
                                if github is None:
                                    st.error(f'Computer B failed and Computer C fallback is unavailable: {exc}')
                                else:
                                    st.warning('Kaggle direct compute failed. Switching to Computer C…')
                                    try:
                                        remote_job = github.submit(root, config)
                                        st.session_state[prefix+'_github_job'] = remote_job
                                        st.session_state.pop(prefix+'_job', None)
                                        st.rerun()
                                    except compute.ComputeBackendError as remote_exc:
                                        st.error(str(remote_exc))
                    else:
                        if github is None:
                            st.error('Computer C is not configured.')
                        else:
                            try:
                                remote_job = github.submit(root, config)
                                st.session_state[prefix+'_github_job'] = remote_job
                                st.session_state.pop(prefix+'_job', None)
                            except compute.ComputeBackendError as exc:
                                st.error(str(exc))
                            else:
                                st.rerun()
                if st.session_state.get(prefix+'_github_job'):
                    show_remote_job(st.session_state[prefix+'_github_job'], prefix+'_job', prefix+'_github_job')
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
                    receptor=job_path(job, config['receptor']).parent/'receptor_prepared.pdb'
                    if config.get('reference'):
                        publication_figure(job, row)
                    else:
                        viewer(pdb=receptor.read_text() if receptor.exists() else None, sdf=sdf)
                    st.download_button('Download results CSV',df.to_csv(index=False),'results.csv','text/csv', on_click='ignore')
                    st.download_button('Download selected pose SDF',sdf,'selected_pose.sdf', on_click='ignore')
                with st.expander('Saved docking settings'):
                    st.json(config)
            manifest()
            ui.automation_marker(st, 'experiment-bundle', state='ready', value='pandoc_experiment.zip')
            st.download_button('Download complete experiment',core.bundle(root),'pandoc_experiment.zip','application/zip', on_click='ignore', key='download_complete_experiment')

    except Exception as exc:
        st.session_state.assistant_diagnostic = str(exc)
        st.error(str(exc))
        with st.expander('Diagnostic details'):
            st.exception(exc)

show_assistant()
