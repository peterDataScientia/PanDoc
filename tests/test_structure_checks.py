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
    assert any('Metal / ion' in i['problem'] for i in issues)
    assert all(i['severity']=='Warning' for i in issues)


def test_changes_distinguish_addition_removal_and_motion():
    before='\n'.join([atom(1,'CA',(0,0,0)),atom(2,'CB',(2,0,0))])
    after='\n'.join([atom(1,'CA',(1,0,0)),atom(2,'O',(3,0,0))])
    changes=structure_checks.changes(before,after)
    assert {r['change'] for r in changes} == {'Added heavy atom','Removed or renamed heavy atom','Moved heavy atom'}


def test_valid_terminal_geometry_passes_coordinate_errors():
    pdb='\n'.join([atom(1,'C',(0,0,0)),atom(2,'O',(1.25,0,0)),atom(3,'OXT',(-0.625,1.083,0))])
    assert not any(i['severity']=='Error' for i in structure_checks.check(pdb)['issues'])


def occupancy(line, value):
    return line[:54]+f'{value:6.2f}'+line[60:]


def test_alternates_recommend_occupancy_and_preserve_shared_atoms():
    from pandoc import core
    pdb='\n'.join([atom(1,'N',(-2,0,0)), occupancy(atom(2,'CA',(0,0,0),alt='A'),.3),
                   occupancy(atom(3,'CA',(.1,0,0),alt='B'),.7), atom(4,'C',(2,0,0))])
    entry=structure_checks.alternate_options(pdb)[0]
    assert entry['recommended']=='B'
    assert not any(i['severity']=='Error' for i in structure_checks.check(pdb,raw=True)['issues'])
    chosen=core.select(pdb,['A:1:CYS'], per_residue={'A:1:CYS':'B'})
    assert len(core.atoms(chosen))==3
    assert all(not a['alt'] for a in core.atoms(chosen))
    assert not structure_checks.changes(pdb,chosen)
    assert core.atoms(core.select(pdb,['A:1:CYS'], alternate='A'))[1]['xyz']==[0,0,0]


def test_missing_names_detected_even_when_count_matches():
    pdb='\n'.join(atom(n,name,(n*2,0,0),res='ALA') for n,name in enumerate(['N','CA','C','O','XX'],1))
    problems=[i['problem'] for i in structure_checks.check(pdb)['issues']]
    assert 'Missing heavy atoms: CB' in problems
    assert 'Unexpected heavy atom names: XX' in problems


def test_selection_filters_connectivity_and_invalid_alternate():
    import pytest
    from pandoc import core
    pdb='\n'.join([atom(1,'CA',(0,0,0),alt='B'), atom(2,'CB',(2,0,0)),
                   atom(3,'CA',(5,0,0),number=2), 'CONECT    1    2    3'])
    chosen=core.select(pdb,['A:1:CYS'])
    assert 'CONECT    1    2' in chosen and 'CONECT    1    2    3' not in chosen
    assert len(core.atoms(chosen))==2
    with pytest.raises(ValueError, match='not available'):
        core.select(pdb,['A:1:CYS'],per_residue={'A:1:CYS':'Z'})


def test_complete_alternate_preferred_and_water_flagged():
    pdb='\n'.join([occupancy(atom(1,'CA',(0,0,0),alt='A'),.8),
        occupancy(atom(2,'CA',(1,0,0),alt='B'),.2), atom(3,'CB',(3,0,0),alt='B'),
        atom(4,'O',(10,0,0),res='HOH',number=4)])
    assert structure_checks.alternate_options(pdb)[0]['recommended']=='B'
    assert any('Water retention' in i['problem'] for i in structure_checks.check(pdb,raw=True)['issues'])


def test_disulfide_record_is_retained_only_with_both_residues():
    from pandoc import core
    line='SSBOND   1 CYS A    1    CYS A    2                          1555   1555  2.03'
    pdb='\n'.join([line, atom(1,'SG',(0,0,0)),atom(2,'SG',(2.03,0,0),number=2)])
    assert structure_checks.inventory(pdb)['connections']==[line]
    assert 'SSBOND' in core.select(pdb,['A:1:CYS','A:2:CYS'])
    assert 'SSBOND' not in core.select(pdb,['A:1:CYS'])
