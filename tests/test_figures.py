import json
import re
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from pandoc.figures import overlay_html


def test_overlay_preserves_fixed_frame_and_omits_hydrogens():
    reference = Chem.AddHs(Chem.MolFromSmiles('CCO'))
    AllChem.EmbedMolecule(reference, randomSeed=4)
    pose = Chem.Mol(reference)
    conf = pose.GetConformer()
    for i in range(pose.GetNumAtoms()):
        point = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (point.x+3, point.y, point.z))
    before = np.array(conf.GetPositions())
    html = overlay_html(reference, pose, seed=2026, rank=1)
    data = json.loads(re.search(r'const data=(.*?), W=', html).group(1))
    assert abs(data['rmsd']-3) < 1e-6
    assert Chem.MolFromMolBlock(data['pose']).GetNumAtoms() == 3
    assert np.array_equal(before, conf.GetPositions())
    assert '3996' in html and '2340' in html
    assert 'high.setView(viewer.getView())' in html
    assert 'addModel(data.reference' in html and "'pdb'" not in html


def test_overlay_escapes_uploaded_script_text():
    mol = Chem.MolFromSmiles('CC')
    AllChem.EmbedMolecule(mol, randomSeed=5)
    mol.SetProp('_Name', '</script><script>alert(1)</script>')
    html = overlay_html(mol, mol, seed=2026, rank=1)
    assert '</script><script>alert' not in html


def test_png_resolution_metadata_and_javascript_syntax(tmp_path):
    import base64
    import io
    import shutil
    import struct
    import subprocess
    import zlib
    import pytest
    from PIL import Image
    from pandoc.figures import _HTML
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser JavaScript unit checks')
    script = _HTML.split('<script>')[-1].split('</script>')[0].replace('__DATA__', '{}')
    subprocess.run([node, '-e', 'new Function('+json.dumps(script)+')'], check=True)
    image = io.BytesIO()
    Image.new('RGB', (20, 10), 'white').save(image, format='PNG')
    function = script[script.index('function dpiPNG'):script.index('async function exportFigure')]
    test = function+'\n(async()=>{const b=dpiPNG(Uint8Array.from(Buffer.from('+json.dumps(base64.b64encode(image.getvalue()).decode())+',"base64")));console.log(Buffer.from(await b.arrayBuffer()).toString("base64"));})();'
    result = subprocess.check_output([node, '-e', test], text=True)
    png = base64.b64decode(result)
    rendered = Image.open(io.BytesIO(png))
    assert rendered.size == (20, 10)
    assert abs(rendered.info['dpi'][0]-600) < .01
    offset = 8
    while offset < len(png):
        length = struct.unpack('>I', png[offset:offset+4])[0]
        chunk = png[offset+4:offset+8+length]
        crc = struct.unpack('>I', png[offset+8+length:offset+12+length])[0]
        assert zlib.crc32(chunk) == crc
        offset += length+12
