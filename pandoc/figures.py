"""Ligand-only publication views; coordinates are never fitted or transformed."""
from __future__ import annotations
import json
from rdkit import Chem
from . import core


def overlay_html(reference, pose, *, seed, rank):
    reference = Chem.RemoveHs(reference)
    pose = Chem.RemoveHs(pose)
    rmsd = core.reference_rmsd(reference, pose)
    payload = json.dumps(dict(reference=Chem.MolToMolBlock(reference),
                              pose=Chem.MolToMolBlock(pose), seed=int(seed),
                              rank=int(rank), rmsd=float(rmsd))).replace('<', '\\u003c')
    return _HTML.replace('__DATA__', payload)


_HTML = r'''<!doctype html><html><head><meta charset="utf-8">
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.2/jspdf.umd.min.js"></script>
<style>body{font:14px Arial;margin:0;color:#16324f}#view{width:666px;height:330px;position:relative;}button{padding:9px;margin:8px 6px 8px 0;cursor:pointer}#legend{height:60px;padding:8px;box-sizing:border-box}#render{position:fixed;left:-20000px;top:0;width:3996px;height:1980px}</style></head>
<body><div style="overflow:auto"><div id="view"></div></div><div id="legend"></div>
<button id="reset">Reset view</button><button id="png">Download PNG · 600 DPI</button><button id="pdf">Download PDF</button>
<p id="status">Drag to rotate both ligands together; scroll to zoom. Original receptor coordinates are preserved.</p>
<div id="render"></div><script>
const data=__DATA__, W=3996,H=2340;
const caption=`Seed ${data.seed} · pose ${data.rank} · heavy-atom RMSD ${data.rmsd.toFixed(3)} Å (no fitting)`;
document.getElementById('legend').textContent='● Crystallographic ligand (green)   ● Redocked ligand (magenta)\n'+caption;
let viewer, highViewer, initialView;
function setup(element){const v=$3Dmol.createViewer(element,{backgroundColor:'white'});
v.addModel(data.reference,'sdf');v.addModel(data.pose,'sdf');
v.setStyle({model:0},{stick:{color:'#188a42',radius:0.18}});
v.setStyle({model:1},{stick:{color:'#cc208e',radius:0.18}});v.zoomTo();v.render();return v;}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
// Insert PNG physical resolution, preserving rendered pixels and valid chunk CRCs.
function dpiPNG(bytes){const body=new Uint8Array(13),dv=new DataView(body.buffer);dv.setUint32(0,Math.round(600/0.0254));dv.setUint32(4,Math.round(600/0.0254));body[8]=1;
const chunk=new Uint8Array(21);new DataView(chunk.buffer).setUint32(0,9);chunk.set([112,72,89,115],4);chunk.set(body.subarray(0,9),8);
let crc=0xffffffff;for(const x of chunk.subarray(4,17)){crc^=x;for(let k=0;k<8;k++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}new DataView(chunk.buffer).setUint32(17,(crc^0xffffffff)>>>0);
const parts=[bytes.subarray(0,8),chunk];let p=8;while(p<bytes.length){const n=new DataView(bytes.buffer,bytes.byteOffset+p,4).getUint32(0)+12;const type=String.fromCharCode(...bytes.subarray(p+4,p+8));if(type!=='pHYs')parts.push(bytes.subarray(p,p+n));p+=n;}return new Blob(parts,{type:'image/png'});}
async function exportFigure(format){let high;
try{document.getElementById('png').disabled=true;document.getElementById('pdf').disabled=true;document.getElementById('status').textContent='Rendering publication image…';
highViewer=highViewer||setup(document.getElementById('render'));high=highViewer;high.setView(viewer.getView());high.render();
const image=new Image();image.src=high.pngURI();await image.decode();
if(image.naturalWidth<W||image.naturalHeight<1980)throw new Error('High-resolution rendering is unavailable on this device. Try a desktop browser.');
const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,W,H);ctx.drawImage(image,0,0,W,1980);
ctx.font='76px Arial';ctx.fillStyle='#188a42';ctx.fillRect(110,2040,65,32);ctx.fillStyle='#16324f';ctx.fillText('Crystallographic ligand',200,2095);
ctx.fillStyle='#cc208e';ctx.fillRect(2120,2040,65,32);ctx.fillStyle='#16324f';ctx.fillText('Redocked ligand',2210,2095);
ctx.font='65px Arial';ctx.fillText(caption,110,2250);
const filename=`redocking_seed_${data.seed}_pose_${data.rank}`;
if(format==='png'){const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));download(dpiPNG(new Uint8Array(await blob.arrayBuffer())),filename+'.png');}
else{if(!window.jspdf)throw new Error('PDF exporter could not load. Download PNG or check your connection.');const pdf=new jspdf.jsPDF({orientation:'landscape',unit:'in',format:[6.66,3.90]});pdf.addImage(canvas.toDataURL('image/png'),'PNG',0,0,6.66,3.90);pdf.save(filename+'.pdf');}
document.getElementById('status').textContent='Exported 6.66 × 3.90 inches; PNG 3996 × 2340 pixels at 600 DPI. PDF contains a raster molecular panel.';
}catch(e){document.getElementById('status').textContent='Export failed: '+e.message;}
finally{document.getElementById('png').disabled=false;document.getElementById('pdf').disabled=false;}}
try{viewer=setup(document.getElementById('view'));initialView=viewer.getView().slice();document.getElementById('reset').onclick=()=>{viewer.setView(initialView);viewer.render();};document.getElementById('png').onclick=()=>exportFigure('png');document.getElementById('pdf').onclick=()=>exportFigure('pdf');}
catch(e){document.getElementById('status').textContent='Viewer could not load. Check internet access to 3Dmol.org. '+e.message;}
</script></body></html>'''
