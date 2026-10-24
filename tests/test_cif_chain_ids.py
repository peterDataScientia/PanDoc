"""Regression tests for long mmCIF chain identifiers in legacy PDB export."""
import gemmi

from pandoc import core


def _structure(chain_names):
    structure = gemmi.Structure()
    structure.name = "long_chains"
    model = gemmi.Model("1")
    for i, name in enumerate(chain_names):
        chain = gemmi.Chain(name)
        residue = gemmi.Residue()
        residue.name = "ALA"
        residue.seqid = gemmi.SeqId("1")
        atom = gemmi.Atom()
        atom.name = "CA"
        atom.element = gemmi.Element("C")
        atom.pos = gemmi.Position(i + 1.0, 2.0, 3.0)
        residue.add_atom(atom)
        chain.add_residue(residue)
        model.add_chain(chain)
    structure.add_model(model)
    return structure.make_mmcif_document().as_string()


def test_long_chain_ids_convert_without_collision():
    pdb = core.normalize_structure(_structure(["AAA", "BBB", "A"]), ".cif")
    atom_lines = [x for x in pdb.splitlines() if x.startswith("ATOM")]
    ids = [line[21:22] for line in atom_lines]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert "A" in ids
    assert "REMARK 950 PANDOC CHAIN_MAP AAA -> " in pdb
    assert "REMARK 950 PANDOC CHAIN_MAP BBB -> " in pdb
    assert [float(line[30:38]) for line in atom_lines] == [1.0, 2.0, 3.0]


def test_existing_single_character_chains_remain_unchanged():
    pdb = core.normalize_structure(_structure(["A", "B"]), ".mmcif")
    atom_lines = [x for x in pdb.splitlines() if x.startswith("ATOM")]
    assert [line[21:22] for line in atom_lines] == ["A", "B"]
    assert "CHAIN_MAP" not in pdb


def test_pdb_input_unchanged():
    text = "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C\nEND\n"
    assert core.normalize_structure(text, ".pdb") == text


def test_multimodel_chain_order_and_membership_change():
    structure = gemmi.Structure()
    structure.name = "ensemble"
    for model_name, names in [("1", ["AAA", "BBB", "A"]), ("2", ["BBB", "AAA"]), ("3", ["AAA", "A", "BBB"])]:
        model = gemmi.Model(model_name)
        for i, name in enumerate(names):
            chain = gemmi.Chain(name)
            residue = gemmi.Residue()
            residue.name = "ALA"
            residue.seqid = gemmi.SeqId("1")
            atom = gemmi.Atom()
            atom.name = "CA"
            atom.element = gemmi.Element("C")
            atom.pos = gemmi.Position(float(i + 1), 2.0, 3.0)
            residue.add_atom(atom)
            chain.add_residue(residue)
            model.add_chain(chain)
        structure.add_model(model)
    pdb = core.normalize_structure(structure.make_mmcif_document().as_string(), ".cif")
    models = []
    current = []
    for line in pdb.splitlines():
        if line.startswith("MODEL"):
            current = []
        elif line.startswith("ATOM"):
            current.append(line[21])
        elif line.startswith("ENDMDL"):
            models.append(current)
    assert len(models) == 3
    assert models[0] == ["B", "C", "A"]
    assert models[1] == ["C", "B"]
    assert models[2] == ["B", "A", "C"]
    assert pdb.count("REMARK 950 PANDOC CHAIN_MAP AAA -> B") == 1
