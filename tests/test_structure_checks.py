from pandoc import structure_checks


def atom(serial, name, xyz, res='CYS', number=1, alt=''):
    x,y,z=xyz
    return f'ATOM  {serial:5d} {name:>4}{alt:1}{res:>3} A{number:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {name[0]:>2}  '


def test_duplicate_and_overlap_blocked():
    pdb='\n'.join([atom(1,'CA',(0,0,0)),atom(2,'CA',(0.1,0,0))])
    issues=structure_checks.check(pdb)['issues']
    assert any('Duplicate' in i['problem'] and i['severity']=='Error' for i in issues)
    assert any('overlap' in i['problem'] for i in issues)


def test_chain_break_and_nonstandard_are_review_warnings():
    pdb='\n'.join([atom(1,'C',(0,0,0)),atom(2,'N',(8,0,0),number=3),atom(3,'ZN',(10,0,0),res='ZN',number=4)])
    issues=structure_checks.check(pdb)['issues']
    assert any('chain break' in i['problem'] for i in issues)
    assert any('nonstandard' in i['problem'] for i in issues)
    assert all(i['severity']=='Warning' for i in issues)


def test_changes_distinguish_addition_removal_and_motion():
    before='\n'.join([atom(1,'CA',(0,0,0)),atom(2,'CB',(2,0,0))])
    after='\n'.join([atom(1,'CA',(1,0,0)),atom(2,'O',(3,0,0))])
    changes=structure_checks.changes(before,after)
    assert {r['change'] for r in changes} == {'Added heavy atom','Removed or renamed heavy atom','Moved heavy atom'}


def test_valid_terminal_geometry_passes_coordinate_errors():
    pdb='\n'.join([atom(1,'C',(0,0,0)),atom(2,'O',(1.25,0,0)),atom(3,'OXT',(-0.625,1.083,0))])
    assert not any(i['severity']=='Error' for i in structure_checks.check(pdb)['issues'])
