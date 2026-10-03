from pathlib import Path
from streamlit.testing.v1 import AppTest
from pandoc import core


def atom(serial, name, xyz, alt='', occupancy=1):
    x,y,z=xyz
    return f'ATOM  {serial:5d} {name:>4}{alt:1}ALA A   1    {x:8.3f}{y:8.3f}{z:8.3f}{occupancy:6.2f} 20.00          {name[0]:>2}  '


def test_alternate_choice_and_issue_panel_reach_preparation():
    pdb='\n'.join([atom(1,'N',(-2,0,0)), atom(2,'CA',(0,0,0), 'A',.3),
        atom(3,'CA',(0,.1,0),'B',.7), atom(4,'C',(2,0,0)),atom(5,'O',(3.2,0,0))])
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    app.session_state['pdb']=pdb
    app.session_state['source_id']=core.digest(pdb)
    app.run()
    assert not list(app.exception)
    alternate=next(s for s in app.selectbox if s.label=='A:1:ALA')
    assert alternate.value=='B'
    next(b for b in app.button if b.label=='Use selection and continue').click().run()
    assert not list(app.exception)
    selected=app.session_state['selected_pdb']
    assert len(core.atoms(selected))==4
    assert not any(a['alt'] for a in core.atoms(selected))
    assert app.session_state['workflow_stage']=='2 · Prepare structures'
    assert not list(app.exception)
    assert any(s.label=='Inspect an issue' for s in app.selectbox)
    assert any(i['problem']=='Missing heavy atoms: CB' for i in app.session_state['assistant_structure_checks']['issues'])
    assert app.session_state['preparation_review']['pH_context']==7


def test_preparation_is_available_without_confirmation_and_repair_is_default():
    pdb='\n'.join([atom(1,'N',(-2,0,0)),atom(2,'CA',(0,0,0)),atom(3,'C',(2,0,0)),atom(4,'O',(3.2,0,0))])
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    app.session_state['selected_pdb']=pdb
    app.session_state['next_stage']='2 · Prepare structures'
    app.run()
    assert not list(app.exception)
    assert not next(b for b in app.button if b.label=='Prepare receptor').disabled
    assert next(c for c in app.checkbox if c.label=='Rebuild missing heavy atoms with PDBFixer').value
    assert not any(c.label=='I reviewed the receptor components and intended protonation states.' for c in app.checkbox)
    assert not next(e for e in app.expander if e.label=='Advanced preparation').proto.expanded
    assert not next(e for e in app.expander if e.label=='Structure details').proto.expanded
