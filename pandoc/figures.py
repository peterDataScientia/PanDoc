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
<style>body{font:14px Arial;margin:0;color:#16324f}#view{width:666px;height:360px;position:relative;}button{padding:9px;margin:8px 6px 8px 0;cursor:pointer}#legend{height:30px;display:flex;align-items:center;justify-content:center;gap:32px;background:white;color:#222}.swatch{display:inline-block;width:18px;height:8px;margin-right:8px;vertical-align:middle}label{margin-right:16px}#render{position:fixed;left:-20000px;top:0;width:3996px;height:2160px}</style></head>
<body><div style="overflow:auto"><div id="view"></div></div><div id="legend"><span><i id="referenceSwatch" class="swatch"></i>Crystallographic</span><span><i id="poseSwatch" class="swatch"></i>Redocked</span></div>
<p><label>Reference <input id="referenceColor" type="color" value="#00cdd4"></label><label>Redocked <input id="poseColor" type="color" value="#d500d5"></label><label>Background <select id="background"><option value="#ffffff">White</option><option value="#2d3336">Dark</option></select></label><label>Style <select id="style"><option value="ball">Ball and stick</option><option value="stick">Sticks</option></select></label></p>
<button id="reset">Reset view</button><button id="png">Download PNG · 600 DPI</button><button id="pdf">Download PDF</button>
<p id="status">Drag to rotate both ligands together; scroll to zoom. Original receptor coordinates are preserved.</p>
<div id="render"></div><script>
const data=__DATA__, W=3996,H=2340;
const caption=`Seed ${data.seed} · pose ${data.rank} · heavy-atom RMSD ${data.rmsd.toFixed(3)} Å (no fitting)`;
// Only the two color labels are drawn inside the publication figure.
let viewer, highViewer, initialView;
function appearance(){return {reference:document.getElementById('referenceColor').value,pose:document.getElementById('poseColor').value,background:document.getElementById('background').value,style:document.getElementById('style').value};}
function applyStyle(v){const a=appearance();v.setBackgroundColor(a.background);
for(const [model,color] of [[0,a.reference],[1,a.pose]]){const style={stick:{color,radius:0.14}};if(a.style==='ball')style.sphere={color,radius:0.28};v.setStyle({model},style);}v.render();}
function updateAppearance(){applyStyle(viewer);const a=appearance();document.getElementById('legend').style.background=a.background;document.getElementById('legend').style.color=a.background==='#ffffff'?'#222':'#f5f5f5';document.getElementById('referenceSwatch').style.background=a.reference;document.getElementById('poseSwatch').style.background=a.pose;}
function setup(element){const v=$3Dmol.createViewer(element,{backgroundColor:appearance().background});
v.addModel(data.reference,'sdf');v.addModel(data.pose,'sdf');
applyStyle(v);v.zoomTo();v.render();return v;}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
// Insert PNG physical resolution, preserving rendered pixels and valid chunk CRCs.
function dpiPNG(bytes){const body=new Uint8Array(13),dv=new DataView(body.buffer);dv.setUint32(0,Math.round(600/0.0254));dv.setUint32(4,Math.round(600/0.0254));body[8]=1;
const chunk=new Uint8Array(21);new DataView(chunk.buffer).setUint32(0,9);chunk.set([112,72,89,115],4);chunk.set(body.subarray(0,9),8);
let crc=0xffffffff;for(const x of chunk.subarray(4,17)){crc^=x;for(let k=0;k<8;k++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}new DataView(chunk.buffer).setUint32(17,(crc^0xffffffff)>>>0);
const parts=[bytes.subarray(0,8)];let p=8;while(p<bytes.length){const n=new DataView(bytes.buffer,bytes.byteOffset+p,4).getUint32(0)+12;const type=String.fromCharCode(...bytes.subarray(p+4,p+8));if(type!=='pHYs')parts.push(bytes.subarray(p,p+n));if(type==='IHDR')parts.push(chunk);p+=n;}return new Blob(parts,{type:'image/png'});}
async function exportFigure(format){let high;
try{document.getElementById('png').disabled=true;document.getElementById('pdf').disabled=true;document.getElementById('status').textContent='Rendering publication image…';
highViewer=highViewer||setup(document.getElementById('render'));high=highViewer;applyStyle(high);high.setView(viewer.getView());high.render();
const image=new Image();image.src=high.pngURI();await image.decode();
if(image.naturalWidth<W||image.naturalHeight<2160)throw new Error('High-resolution rendering is unavailable on this device. Try a desktop browser.');
const a=appearance(),canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;const ctx=canvas.getContext('2d');ctx.fillStyle=a.background;ctx.fillRect(0,0,W,H);ctx.drawImage(image,0,0,W,2160);
// Center a compact two-item legend; metadata belongs to the manuscript caption.
ctx.font='76px Arial';ctx.textBaseline='middle';const swatch=108,gap=48,between=192;
const labels=['Crystallographic','Redocked'],colors=[a.reference,a.pose];
const widths=labels.map(label=>swatch+gap+ctx.measureText(label).width);
let x=(W-widths[0]-widths[1]-between)/2;
for(let i=0;i<2;i++){ctx.fillStyle=colors[i];ctx.fillRect(x,2250-24,swatch,48);ctx.fillStyle=a.background==='#ffffff'?'#222':'#f5f5f5';ctx.fillText(labels[i],x+swatch+gap,2250);x+=widths[i]+between;}
const filename=`redocking_seed_${data.seed}_pose_${data.rank}`;
if(format==='png'){const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));download(dpiPNG(new Uint8Array(await blob.arrayBuffer())),filename+'.png');}
else{if(!window.jspdf)throw new Error('PDF exporter could not load. Download PNG or check your connection.');const pdf=new jspdf.jsPDF({orientation:'landscape',unit:'in',format:[6.66,3.90]});pdf.addImage(canvas.toDataURL('image/png'),'PNG',0,0,6.66,3.90);pdf.save(filename+'.pdf');}
document.getElementById('status').textContent='Exported 6.66 × 3.90 inches; PNG 3996 × 2340 pixels at 600 DPI. PDF contains a raster molecular panel.';
}catch(e){document.getElementById('status').textContent='Export failed: '+e.message;}
finally{document.getElementById('png').disabled=false;document.getElementById('pdf').disabled=false;}}
try{viewer=setup(document.getElementById('view'));initialView=viewer.getView().slice();updateAppearance();for(const id of ['referenceColor','poseColor','background','style'])document.getElementById(id).onchange=updateAppearance;document.getElementById('reset').onclick=()=>{viewer.setView(initialView);viewer.render();};document.getElementById('png').onclick=()=>exportFigure('png');document.getElementById('pdf').onclick=()=>exportFigure('pdf');}
catch(e){document.getElementById('status').textContent='Viewer could not load. Check internet access to 3Dmol.org. '+e.message;}
</script></body></html>'''
