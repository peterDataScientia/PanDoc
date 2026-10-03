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
    assert 'drawScene(canvas)' in html
    assert 'borderEnabled' in html and 'borderColor' in html and 'borderWidth' in html
    assert 'borderRadius' in html and 'quadraticCurveTo' in html
    assert len(data['models']) == 2
    assert np.allclose(data['models'][1]['atoms'], Chem.RemoveHs(pose).GetConformer().GetPositions())
    assert "'pdb'" not in html


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
    assert png[12:16] == b'IHDR'
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




def test_pdf_export_without_external_library(tmp_path):
    import base64
    import shutil
    import subprocess
    import zlib
    import pytest
    from pandoc.figures import _HTML
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser JavaScript checks')
    function = _HTML[_HTML.index('async function pdfBlob'): _HTML.index('async function exportFigure')]
    assert 'jspdf' not in _HTML and 'cdnjs' not in _HTML
    for compressed in (True, False):
        setup = '' if compressed else 'globalThis.CompressionStream=undefined;'
        script = setup+function+'''\n(async()=>{const canvas={width:2,height:1,getContext:()=>({getImageData:()=>({data:new Uint8Array([255,0,0,255,0,255,0,255])})})};const b=await pdfBlob(canvas);console.log(Buffer.from(await b.arrayBuffer()).toString('base64'));})();'''
        pdf = base64.b64decode(subprocess.check_output([node, '-e', script], text=True))
        assert b'/MediaBox [0 0 479.52 280.8]' in pdf
        xref = int(pdf.split(b'startxref\n')[1].splitlines()[0])
        assert pdf[xref:xref+4] == b'xref'
        entries = pdf[xref:].splitlines()[3:8]
        for index, entry in enumerate(entries, 1):
            offset = int(entry[:10])
            assert pdf[offset:].startswith(f'{index} 0 obj'.encode())
        image_object = pdf.split(b'5 0 obj\n')[1].split(b'\nendobj')[0]
        header, stream = image_object.split(b'\nstream\n')
        pixels = stream.split(b'\nendstream')[0]
        if b'/FlateDecode' in header:
            pixels = zlib.decompress(pixels)
        assert pixels == bytes([255,0,0,0,255,0])
        (tmp_path/('compressed.pdf' if compressed else 'raw.pdf')).write_bytes(pdf)



