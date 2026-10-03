"""Ligand-only publication views; coordinates are never fitted or transformed."""
from __future__ import annotations
import json
from rdkit import Chem
from . import core


def overlay_html(reference, pose, *, seed, rank):
    reference = Chem.RemoveHs(reference)
    pose = Chem.RemoveHs(pose)
    rmsd = core.reference_rmsd(reference, pose)
    def scene(mol):
        conf = mol.GetConformer()
        return dict(atoms=[list(conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())],
                    bonds=[[b.GetBeginAtomIdx(), b.GetEndAtomIdx(), 1 if b.GetIsAromatic() else int(b.GetBondTypeAsDouble())] for b in mol.GetBonds()])
    payload = json.dumps(dict(models=[scene(reference), scene(pose)], reference=Chem.MolToMolBlock(reference),
                              pose=Chem.MolToMolBlock(pose), seed=int(seed),
                              rank=int(rank), rmsd=float(rmsd))).replace('<', '\\u003c')
    return _HTML.replace('__DATA__', payload)


_HTML = r'''<!doctype html><html><head><meta charset="utf-8">
<style>body{font:14px Arial;margin:0;color:#16324f}#scene{width:666px;height:390px;touch-action:none;cursor:grab}button{padding:9px;margin:8px 6px 8px 0;cursor:pointer}label{margin-right:12px}</style></head><body>
<div style="overflow:auto"><canvas id="scene" width="1332" height="780"></canvas></div>
<p><label>Reference <input id="referenceColor" type="color" value="#00cdd4"></label><label>Redocked <input id="poseColor" type="color" value="#d500d5"></label><label>Background <select id="background"><option value="#ffffff">White</option><option value="#2d3336">Dark</option></select></label><label>Style <select id="style"><option value="ball">Ball and stick</option><option value="stick">Sticks</option></select></label></p>
<button type="button" id="reset">Reset view</button><button type="button" id="png">Download PNG · 600 DPI</button><button type="button" id="pdf">Download PDF</button>
<div id="downloads"></div><details id="export-preview" style="display:none"><summary>Preview exported PNG</summary><img id="export-image" alt="Exported publication PNG" style="max-width:666px;width:100%;height:auto"></details><p id="status">Drag to rotate; Shift-drag to move; scroll to zoom. Both ligands move together.</p>
<script>
const data=__DATA__, W=3996,H=2340;
// Original coordinates are immutable; only this shared camera changes.
const points=data.models.flatMap(m=>m.atoms),bounds=[0,1,2].map(k=>[Math.min(...points.map(p=>p[k])),Math.max(...points.map(p=>p[k]))]);
const center=bounds.map(b=>(b[0]+b[1])/2),radius=Math.max(...points.map(p=>Math.hypot(...p.map((v,k)=>v-center[k]))),1);
const baseScale=Math.min(570/Math.max(bounds[0][1]-bounds[0][0],1),285/Math.max(bounds[1][1]-bounds[1][0],1));
const camera={yaw:0,pitch:0,zoom:1,x:0,y:0};
function appearance(){return {reference:document.getElementById('referenceColor').value,pose:document.getElementById('poseColor').value,background:document.getElementById('background').value,style:document.getElementById('style').value};}
function tint(color,factor){const values=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16));return 'rgb('+values.map(v=>Math.round(Math.min(255,v*factor))).join(',')+')';}
function project(p){const x=p[0]-center[0],y=p[1]-center[1],z=p[2]-center[2],cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch);
const a=cy*x+sy*z,b=-sy*x+cy*z,scale=baseScale*camera.zoom;
return {x:333+camera.x+a*scale,y:180+camera.y-(cp*y-sp*b)*scale,z:sp*y+cp*b,scale};}
function drawScene(canvas){const ctx=canvas.getContext('2d',{alpha:false,willReadFrequently:true}),factor=canvas.width/666;
ctx.setTransform(factor,0,0,factor,0,0);const colors=appearance();ctx.fillStyle=colors.background;ctx.fillRect(0,0,666,390);
ctx.save();ctx.beginPath();ctx.rect(0,0,666,360);ctx.clip();const objects=[];
for(let model=0;model<2;model++){const molecule=data.models[model],atoms=molecule.atoms.map(project),color=model===0?colors.reference:colors.pose;
for(const [ia,ib,order] of molecule.bonds){const a=atoms[ia],b=atoms[ib],dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1;
for(let bond=0;bond<Math.max(1,order);bond++){const offset=(bond-(Math.max(1,order)-1)/2)*0.19*a.scale,ox=-dy/len*offset,oy=dx/len*offset;
for(let n=0;n<12;n++){const t=n/12,u=(n+1)/12;objects.push({type:'bond',z:a.z+(b.z-a.z)*(t+u)/2,color,r:0.11*a.scale,a:{x:a.x+dx*t+ox,y:a.y+dy*t+oy},b:{x:a.x+dx*u+ox,y:a.y+dy*u+oy}});}}}
for(const a of atoms)objects.push({type:'atom',...a,color,r:(colors.style==='ball'?0.28:0.13)*a.scale});}
objects.sort((a,b)=>a.z-b.z);
for(const o of objects){if(o.type==='bond'){ctx.lineCap='round';ctx.strokeStyle=tint(o.color,0.62);ctx.lineWidth=o.r*2;ctx.beginPath();ctx.moveTo(o.a.x,o.a.y);ctx.lineTo(o.b.x,o.b.y);ctx.stroke();ctx.strokeStyle=o.color;ctx.lineWidth=o.r*1.45;ctx.stroke();}
else{const g=ctx.createRadialGradient(o.x-o.r*0.35,o.y-o.r*0.35,o.r*0.03,o.x,o.y,o.r);g.addColorStop(0,'#f0ffff');g.addColorStop(0.22,tint(o.color,1.12));g.addColorStop(0.62,o.color);g.addColorStop(1,tint(o.color,0.48));ctx.fillStyle=g;ctx.beginPath();ctx.arc(o.x,o.y,o.r,0,Math.PI*2);ctx.fill();}}
ctx.restore();ctx.font='13px Arial';ctx.textBaseline='middle';const labels=['Crystallographic','Redocked'],palette=[colors.reference,colors.pose],widths=labels.map(label=>26+ctx.measureText(label).width);let x=(666-widths[0]-widths[1]-32)/2;
for(let i=0;i<2;i++){ctx.fillStyle=palette[i];ctx.fillRect(x,371,18,8);ctx.fillStyle=colors.background==='#ffffff'?'#222':'#f5f5f5';ctx.fillText(labels[i],x+26,375);x+=widths[i]+32;}
return canvas;}
const preview=document.getElementById('scene');
function refresh(){drawScene(preview);}
let drag=null;
preview.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY};preview.setPointerCapture(e.pointerId);};
preview.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(e.shiftKey){camera.x+=dx;camera.y+=dy;}else{camera.yaw+=dx*0.012;camera.pitch+=dy*0.012;}drag={x:e.clientX,y:e.clientY};refresh();};
preview.onpointerup=()=>{drag=null;};preview.onpointercancel=()=>{drag=null;};
preview.addEventListener('wheel',e=>{e.preventDefault();camera.zoom=Math.max(0.2,Math.min(5,camera.zoom*Math.exp(-e.deltaY*0.001)));refresh();},{passive:false});
const exportUrls={};
function download(blob,name){const type=blob.type==='application/pdf'?'pdf':'png';if(exportUrls[type])URL.revokeObjectURL(exportUrls[type]);
const url=URL.createObjectURL(blob);exportUrls[type]=url;const id='save-'+type;document.getElementById(id)?.remove();
const a=document.createElement('a');a.id=id;a.href=url;a.download=name;a.textContent='Save '+type.toUpperCase();a.style.marginRight='20px';document.getElementById('downloads').appendChild(a);if(type==='png'){document.getElementById('export-image').src=url;document.getElementById('export-preview').style.display='block';}a.click();}
// Insert PNG physical resolution, preserving rendered pixels and valid chunk CRCs.
function dpiPNG(bytes){const body=new Uint8Array(13),dv=new DataView(body.buffer);dv.setUint32(0,Math.round(600/0.0254));dv.setUint32(4,Math.round(600/0.0254));body[8]=1;
const chunk=new Uint8Array(21);new DataView(chunk.buffer).setUint32(0,9);chunk.set([112,72,89,115],4);chunk.set(body.subarray(0,9),8);
let crc=0xffffffff;for(const x of chunk.subarray(4,17)){crc^=x;for(let k=0;k<8;k++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}new DataView(chunk.buffer).setUint32(17,(crc^0xffffffff)>>>0);
const parts=[bytes.subarray(0,8)];let p=8;while(p<bytes.length){const n=new DataView(bytes.buffer,bytes.byteOffset+p,4).getUint32(0)+12;const type=String.fromCharCode(...bytes.subarray(p+4,p+8));if(type!=='pHYs')parts.push(bytes.subarray(p,p+n));if(type==='IHDR')parts.push(chunk);p+=n;}return new Blob(parts,{type:'image/png'});}
// A single-page lossless RGB PDF, generated locally with no script dependency.
async function pdfBlob(canvas){
const rgba=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
const rgb=new Uint8Array(canvas.width*canvas.height*3);
for(let i=0,j=0;i<rgba.length;i+=4){rgb[j++]=rgba[i];rgb[j++]=rgba[i+1];rgb[j++]=rgba[i+2];}
let image=rgb,filter='';
if(typeof CompressionStream!=='undefined'){
try{image=new Uint8Array(await new Response(new Blob([rgb]).stream().pipeThrough(new CompressionStream('deflate'))).arrayBuffer());filter='/Filter /FlateDecode ';}catch(e){image=rgb;}}
const encoder=new TextEncoder(),parts=[],offsets=[0];let length=0;
function append(value){const bytes=typeof value==='string'?encoder.encode(value):value;parts.push(bytes);length+=bytes.length;}
function object(number,body,stream){offsets[number]=length;append(`${number} 0 obj\n${body}`);if(stream){append('\nstream\n');append(stream);append('\nendstream');}append('\nendobj\n');}
append('%PDF-1.4\n');append(new Uint8Array([37,226,227,207,211,10]));
object(1,'<< /Type /Catalog /Pages 2 0 R >>');
object(2,'<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
object(3,'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 479.52 280.8] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>');
const content=encoder.encode('q\n479.52 0 0 280.8 0 0 cm\n/Im0 Do\nQ\n');
object(4,`<< /Length ${content.length} >>`,content);
object(5,`<< /Type /XObject /Subtype /Image /Width ${canvas.width} /Height ${canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 ${filter}/Length ${image.length} >>`,image);
const xref=length;append('xref\n0 6\n0000000000 65535 f \n');
for(let i=1;i<=5;i++)append(String(offsets[i]).padStart(10,'0')+' 00000 n \n');
append(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
return new Blob(parts,{type:'application/pdf'});
}
async function exportFigure(format){
try{document.getElementById('png').disabled=true;document.getElementById('pdf').disabled=true;document.getElementById('status').textContent='Rendering publication image…';
const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;drawScene(canvas);
const filename=`redocking_seed_${data.seed}_pose_${data.rank}`;
if(format==='png'){const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('PNG encoding failed. Try exporting again.');download(dpiPNG(new Uint8Array(await blob.arrayBuffer())),filename+'.png');}
else{download(await pdfBlob(canvas),filename+'.pdf');}
document.getElementById('status').textContent='Exported 6.66 × 3.90 inches; PNG 3996 × 2340 pixels at 600 DPI. PDF contains a raster molecular panel.';
}catch(e){document.getElementById('status').textContent='Export failed: '+e.message;}
finally{document.getElementById('png').disabled=false;document.getElementById('pdf').disabled=false;}}
document.getElementById('reset').onclick=()=>{Object.assign(camera,{yaw:0,pitch:0,zoom:1,x:0,y:0});refresh();};
for(const id of ['referenceColor','poseColor','background','style'])document.getElementById(id).onchange=refresh;
document.getElementById('png').onclick=()=>exportFigure('png');document.getElementById('pdf').onclick=()=>exportFigure('pdf');refresh();
</script></body></html>'''
