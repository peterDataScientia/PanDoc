"""Reliable standalone 3Dmol.js viewer for Streamlit iframe components.

The viewer uses an explicit CDN fallback and reports loading/WebGL/model
errors inside the iframe instead of leaving an unexplained blank canvas.
"""
from __future__ import annotations

import json
import uuid

from . import core

# Independent CDN hosts. Keep 3Dmol.js version fixed across sources.
_3DMOL_CDNS = (
    "https://cdn.jsdelivr.net/npm/3dmol@2.5.5/build/3Dmol-min.js",
    "https://unpkg.com/3dmol@2.5.5/build/3Dmol-min.js",
)


def viewer_html(pdb=None, sdf=None, reference=None, center=None, size=None, focus=None):
    """Return a self-contained HTML document for components.html()."""
    models = []
    serials = []
    if pdb:
        # Validate the actual structure instead of silently drawing an empty model.
        atoms = core.atoms(pdb)
        models.append({"format": "pdb", "kind": "protein", "data": pdb})
        if focus:
            serials = [
                int(atom["line"][6:11])
                for atom in atoms
                if core.key(atom) == focus
            ]
    if reference:
        models.append({"format": "sdf", "kind": "reference", "data": reference})
    if sdf:
        models.append({"format": "sdf", "kind": "candidate", "data": sdf})
    if not models:
        return '<p role="alert">No structure supplied to the molecular viewer.</p>'

    dims = None
    if center is not None and size is not None:
        if len(center) != 3 or len(size) != 3:
            raise ValueError("Docking box center and size must each have three coordinates.")
        dims = {
            "center": dict(zip("xyz", map(float, center))),
            "dimensions": dict(zip("whd", map(float, size))),
        }

    payload = {
        "models": models,
        "focus_serials": serials,
        "box": dims,
        "cdn_urls": _3DMOL_CDNS,
    }
    # Escape HTML special characters because JSON is embedded inside <script>.
    encoded = (json.dumps(payload, ensure_ascii=True)
               .replace("<", "\\u003c")
               .replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    widget_id = "pandoc3d_" + uuid.uuid4().hex[:12]

    html = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
html, body { margin:0; padding:0; background:#fff; color:#26384b;
             font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
#VIEW_ID_scene { position:relative; width:100%; height:430px;
                 background:#fff; overflow:hidden; }
#VIEW_ID_status { position:absolute; top:10px; left:10px; right:10px;
                  z-index:10; padding:9px 12px; font-size:13px;
                  border:1px solid #cbd5e1; border-radius:7px;
                  background:#f8fafc; pointer-events:none; }
#VIEW_ID_status.error { background:#fff0ee; color:#9f1239;
                         border-color:#fda4af; pointer-events:auto; }
#VIEW_ID_status.ready { display:none; }
#VIEW_ID_status.fallback { top:7px;right:7px;left:auto;max-width:80%;
  background:#edf5f7; color:#24546a; border-color:#c3dce5; font-size:11px; }
#VIEW_ID_toolbar { display:flex; gap:6px; flex-wrap:wrap;
                   padding:5px 2px; align-items:center; }
#VIEW_ID_toolbar button { padding:5px 11px; border-radius:6px;
                          border:1px solid #cbd5e1; background:#f8fafc;
                          color:#26384b; cursor:pointer; font-size:13px; }
#VIEW_ID_toolbar button:disabled { opacity:.5; cursor:not-allowed; }
#VIEW_ID_toolbar label { font-size:13px; font-weight:650; color:#26384b; }
#VIEW_ID_toolbar select { appearance:auto; min-width:120px; padding:5px 9px;
    border:1px solid #cbd5e1; border-radius:6px; color:#26384b; background:#fff;
    font:13px system-ui,-apple-system,"Segoe UI",sans-serif; cursor:pointer; }
#VIEW_ID_retry { display:none; }
</style>
</head>
<body>
<div id="VIEW_ID_scene">
  <div id="VIEW_ID_status" role="status" aria-live="polite">Loading molecular viewer...</div>
</div>
<div id="VIEW_ID_toolbar">
  <label for="VIEW_ID_style">Protein style</label>
  <select id="VIEW_ID_style" aria-label="Protein display style">
    <option value="cartoon" selected>Cartoon</option>
    <option value="stick">Stick</option>
    <option value="sphere">Sphere</option>
    <option value="line">Line</option>
  </select>
  <button id="VIEW_ID_zoom_in" type="button" disabled>Zoom +</button>
  <button id="VIEW_ID_zoom_out" type="button" disabled>Zoom -</button>
  <button id="VIEW_ID_fit" type="button" disabled>Fit structure</button>
  <button id="VIEW_ID_retry" type="button">Retry viewer</button>
</div>
<script>
(function () {
  "use strict";
  const data = __PANDOC_VIEWER_DATA__;
  const scene = document.getElementById("VIEW_ID_scene");
  const status = document.getElementById("VIEW_ID_status");
  const retry = document.getElementById("VIEW_ID_retry");
  const zoomIn = document.getElementById("VIEW_ID_zoom_in");
  const zoomOut = document.getElementById("VIEW_ID_zoom_out");
  const fit = document.getElementById("VIEW_ID_fit");
  const styleSelect = document.getElementById("VIEW_ID_style");
  let viewer = null;
  let fallbackControls = null;

  function errorMessage(error) {
    const message = (error && error.message) ? error.message : String(error);
    status.className = "error";
    status.textContent = "Unable to display 3D structure: " + message +
      ". Check browser WebGL and whether 3Dmol.js CDNs are blocked.";
    retry.style.display = "inline-block";
    console.error("PanDoc molecular viewer:", error);
  }

  function webGLAvailable() {
    try {
      const canvas = document.createElement("canvas");
      return !!(canvas.getContext("webgl2") ||
                canvas.getContext("webgl") ||
                canvas.getContext("experimental-webgl"));
    } catch (_) {
      return false;
    }
  }


  // CPU-rendered, rotatable molecular preview for browsers without WebGL.
  // Uses the deposited coordinates, CA backbone and explicit SDF/PDB bonds.
  function createCanvasFallback(reason) {
    const atoms = [], bonds = [], traces = [];
    const bondKeys = new Set();
    function addAtom(x, y, z, element, kind, chain, group, serial) {
      if (![x,y,z].every(Number.isFinite)) return -1;
      const index = atoms.length;
      atoms.push({x:x, y:y, z:z, element:(element || "C").toUpperCase(),
                  kind:kind, chain:chain || "", group:group || "", serial:serial,
                  name:""});
      return index;
    }
    function addBond(a,b,style) {
      if (!Number.isInteger(a) || !Number.isInteger(b) ||
          !atoms[a] || !atoms[b] || a === b) return;
      const key = Math.min(a,b) + ":" + Math.max(a,b);
      if (bondKeys.has(key)) return;
      bondKeys.add(key);
      bonds.push({a:a,b:b,style:style || "stick"});
    }
    function distance(a,b) {
      const x=a.x-b.x, y=a.y-b.y, z=a.z-b.z;
      return Math.sqrt(x*x+y*y+z*z);
    }
    function parsePdb(text) {
      const lines=text.split(/\r?\n/), serialMap=new Map();
      let prior=null, tracesFound=0;
      const heteroGroups=new Map();
      const proteinGroups=new Map();
      lines.forEach(function(line) {
        const rec=line.slice(0,6).trim();
        if (rec !== "ATOM" && rec !== "HETATM") return;
        const residue=line.slice(17,20).trim();
        if (rec==="HETATM" && ["HOH","WAT","DOD"].includes(residue)) return;
        const name=line.slice(12,16).trim();
        const x=parseFloat(line.slice(30,38));
        const y=parseFloat(line.slice(38,46));
        const z=parseFloat(line.slice(46,54));
        let el=line.slice(76,78).trim().toUpperCase();
        if (!el) el=name.replace(/[0-9]/g,"").slice(0,1).toUpperCase() || "C";
        if (el==="H" || el==="D") return;
        const serial=parseInt(line.slice(6,11),10);
        const chain=line.slice(21,22);
        const group=chain+":"+line.slice(22,27)+":"+residue;
        if (rec==="ATOM") {
          const idx=addAtom(x,y,z,el,name==="CA"?"trace":"protein",
                            chain,group,serial);
          if (idx>=0) {
            atoms[idx].name=name;
            serialMap.set(serial,idx);
            if (!proteinGroups.has(group)) proteinGroups.set(group,[]);
            proteinGroups.get(group).push(idx);
            if (name==="CA") {
              if (prior && prior.chain===chain &&
                  distance(atoms[prior.idx],atoms[idx])<5.3) {
                addBond(prior.idx,idx,"trace");
              }
              prior={idx:idx,chain:chain};
              tracesFound++;
            }
          }
        } else {
          const idx=addAtom(x,y,z,el,"hetero",chain,group,serial);
          if (idx>=0) {
            serialMap.set(serial,idx);
            if (!heteroGroups.has(group)) heteroGroups.set(group,[]);
            heteroGroups.get(group).push(idx);
          }
        }
      });
      // Build atomic protein connectivity only within residues (plus peptide C-N).
      // This lets non-WebGL Stick/Line/Sphere show actual protein atoms,
      // while Cartoon draws the lighter-weight connected C-alpha trace.
      let previousGroup=null;
      proteinGroups.forEach(function(ids,group) {
        for(let i=0;i<ids.length;i++) for(let j=i+1;j<ids.length;j++) {
          const a=atoms[ids[i]],b=atoms[ids[j]];
          const max=(a.element==="S"||b.element==="S")?2.18:1.95;
          const d=distance(a,b);
          if(d>0.55&&d<max) addBond(ids[i],ids[j],"protein");
        }
        if(previousGroup && previousGroup.group!==group &&
           atoms[previousGroup.ids[0]].chain===atoms[ids[0]].chain) {
          const carb=previousGroup.ids.find(function(id) {
            return atoms[id].serial !== null && atoms[id].element==="C" &&
                   atoms[id].name==="C";
          });
          const nit=ids.find(function(id) {
            return atoms[id].name==="N";
          });
          if(carb!==undefined && nit!==undefined &&
             distance(atoms[carb],atoms[nit])<1.8) addBond(carb,nit,"protein");
        }
        previousGroup={group:group,ids:ids};
      });
      lines.forEach(function(line) {
        if (line.slice(0,6).trim()!=="CONECT") return;
        const ids=(line.slice(6).match(/.{1,5}/g)||[])
          .map(function(s){return parseInt(s.trim(),10);});
        const from=serialMap.get(ids[0]);
        ids.slice(1).forEach(function(n) {
          const to=serialMap.get(n);
          if (from!==undefined && to!==undefined) addBond(from,to,"stick");
        });
      });
      heteroGroups.forEach(function(ids) {
        for (let i=0;i<ids.length;i++) for (let j=i+1;j<ids.length;j++) {
          const a=atoms[ids[i]], b=atoms[ids[j]];
          const max=(a.element==="S"||b.element==="S")?2.15:1.90;
          const d=distance(a,b);
          if (d>0.50 && d<max) addBond(ids[i],ids[j],"stick");
        }
      });
    }
    function parseSdf(text, kind) {
      const lines=text.split("$$")[0].split(/\r?\n/);
      if (lines.length<5) return;
      if (lines[3].includes("V3000")) {
        const ids=new Map();
        let mode="";
        lines.forEach(function(line) {
          if (line.includes("M  V30 BEGIN ATOM")) {mode="atom";return;}
          if (line.includes("M  V30 END ATOM")) {mode="";return;}
          if (line.includes("M  V30 BEGIN BOND")) {mode="bond";return;}
          if (line.includes("M  V30 END BOND")) {mode="";return;}
          if (!line.includes("M  V30 ") || !mode) return;
          const tokens=line.split("M  V30 ")[1].trim().split(/\s+/);
          if (mode==="atom" && tokens.length>=5) {
            const idx=addAtom(+tokens[2],+tokens[3],+tokens[4],
                              tokens[1],kind,"",kind,null);
            if (idx>=0) ids.set(+tokens[0],idx);
          } else if (mode==="bond" && tokens.length>=4) {
            const a=ids.get(+tokens[2]), b=ids.get(+tokens[3]);
            if (a!==undefined&&b!==undefined) addBond(a,b,"stick");
          }
        });
      } else {
        const n=parseInt(lines[3].slice(0,3),10);
        const m=parseInt(lines[3].slice(3,6),10);
        if (!Number.isInteger(n) || n<1 || n>100000) return;
        const ids=[];
        for(let i=0;i<n;i++) {
          const line=lines[4+i]||"";
          ids.push(addAtom(parseFloat(line.slice(0,10)),parseFloat(line.slice(10,20)),
            parseFloat(line.slice(20,30)),line.slice(31,34).trim(),kind,"",kind,null));
        }
        for(let i=0;i<m;i++) {
          const line=lines[4+n+i]||"";
          addBond(ids[parseInt(line.slice(0,3),10)-1],
                  ids[parseInt(line.slice(3,6),10)-1],"stick");
        }
      }
    }
    data.models.forEach(function(model) {
      if (model.format==="pdb") parsePdb(model.data);
      else if (model.format==="sdf") parseSdf(model.data,model.kind);
    });
    if (!atoms.length) throw new Error("No coordinates available for non-WebGL rendering");

    // Do not use a WebGL context. HTML Canvas 2D works with software rendering.
    viewer = null;
    Array.from(scene.children).forEach(function(node) {
      if (node !== status) node.remove();
    });
    const canvas=document.createElement("canvas");
    canvas.dataset.pandocRenderer="2d";
    canvas.style.cssText="width:100%;height:100%;display:block;touch-action:none;cursor:grab";
    scene.insertBefore(canvas,status);
    const ctx=canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D rendering is unavailable");
    const bounds={minX:Infinity,minY:Infinity,minZ:Infinity,
                  maxX:-Infinity,maxY:-Infinity,maxZ:-Infinity};
    atoms.forEach(function(a){
      bounds.minX=Math.min(bounds.minX,a.x); bounds.minY=Math.min(bounds.minY,a.y);
      bounds.minZ=Math.min(bounds.minZ,a.z); bounds.maxX=Math.max(bounds.maxX,a.x);
      bounds.maxY=Math.max(bounds.maxY,a.y); bounds.maxZ=Math.max(bounds.maxZ,a.z);
    });
    const center=[
      (bounds.minX+bounds.maxX)/2,
      (bounds.minY+bounds.maxY)/2,
      (bounds.minZ+bounds.maxZ)/2
    ];
    const maxExtent=Math.max(bounds.maxX-bounds.minX,bounds.maxY-bounds.minY,
                             bounds.maxZ-bounds.minZ,1);
    const state={ax:0.23,ay:-0.42,zoom:1,down:false,x:0,y:0};

    function projected(a,w,h,scale) {
      const x=a.x-center[0],y=a.y-center[1],z=a.z-center[2];
      const cy=Math.cos(state.ay),sy=Math.sin(state.ay);
      const cx=Math.cos(state.ax),sx=Math.sin(state.ax);
      const xx=x*cy+z*sy;
      const zz=-x*sy+z*cy;
      const yy=y*cx-zz*sx;
      return {x:w/2+xx*scale,y:h/2-yy*scale,z:y*sx+zz*cx};
    }
    function atomColor(a) {
      if (a.kind==="trace") return "#6888ab";
      if (a.kind==="reference") return "#279b65";
      if (a.kind==="candidate") return "#c34a9b";
      const el=a.element;
      return {C:"#17a9a5",N:"#316dbd",O:"#df4b4b",S:"#d0a100",
              P:"#dc8d30",F:"#67b06c",CL:"#54a75c",FE:"#a96830"}[el] ||
             "#8395a9";
    }
    function draw() {
      const w=canvas.clientWidth,h=canvas.clientHeight;
      if (w<1||h<1) return;
      const ratio=Math.min(window.devicePixelRatio||1,2);
      const cw=Math.max(1,Math.round(w*ratio)),ch=Math.max(1,Math.round(h*ratio));
      if(canvas.width!==cw || canvas.height!==ch){canvas.width=cw;canvas.height=ch;}
      ctx.setTransform(ratio,0,0,ratio,0,0);
      ctx.clearRect(0,0,w,h);
      ctx.fillStyle="#fff";ctx.fillRect(0,0,w,h);
      const scale=Math.min(w,h)*0.73/maxExtent*state.zoom;
      const projectedAtoms=atoms.map(function(a){return projected(a,w,h,scale);});
      const mode=styleSelect.value;
      const hasBackbone=bonds.some(function(b){return b.style==="trace";});
      const edges=bonds.map(function(b){
        return {a:projectedAtoms[b.a],b:projectedAtoms[b.b],
                kind:b.style,depth:(projectedAtoms[b.a].z+projectedAtoms[b.b].z)/2,
                atomA:atoms[b.a],atomB:atoms[b.b]};
      }).filter(function(e){
        if (e.kind==="trace") return mode==="cartoon";
        if (e.kind==="protein") return mode==="stick" || mode==="line";
        return true;
      }).sort(function(a,b){return a.depth-b.depth;});
      ctx.lineCap="round";
      edges.forEach(function(e) {
        if (e.kind==="trace") {
          // A smooth shaded ribbon-like backbone preview in Canvas 2D.
          // True secondary-structure cartoons require the WebGL viewer.
          ctx.globalAlpha=0.9;
          ctx.strokeStyle="#3b6287";
          ctx.lineWidth=9;
          ctx.beginPath();ctx.moveTo(e.a.x,e.a.y);ctx.lineTo(e.b.x,e.b.y);ctx.stroke();
          ctx.strokeStyle="#83aacb";
          ctx.lineWidth=5.5;
          ctx.beginPath();ctx.moveTo(e.a.x,e.a.y);ctx.lineTo(e.b.x,e.b.y);ctx.stroke();
        } else {
          ctx.strokeStyle=e.kind==="protein" ? "#7f9bb9" : atomColor(e.atomA);
          ctx.globalAlpha=e.kind==="protein"?0.80:0.95;
          ctx.lineWidth=mode==="line"?1.1:3.0;
          ctx.beginPath();ctx.moveTo(e.a.x,e.a.y);ctx.lineTo(e.b.x,e.b.y);ctx.stroke();
        }
      });
      ctx.globalAlpha=1;
      const dots=atoms.map(function(a,i){return {atom:a,p:projectedAtoms[i]};})
        .filter(function(e){
          return e.atom.kind==="hetero" || e.atom.kind==="reference" ||
                 e.atom.kind==="candidate" || mode!=="cartoon" || !hasBackbone;
        })
        .sort(function(a,b){return a.p.z-b.p.z;});
      dots.forEach(function(e){
        ctx.fillStyle=atomColor(e.atom);
        const radius=(mode==="sphere" && 
          (e.atom.kind==="protein" || e.atom.kind==="trace"))?5.7:
          (mode==="line"?1.3:3.0);
        ctx.beginPath();ctx.arc(e.p.x,e.p.y,radius,0,Math.PI*2);ctx.fill();
      });
      // Protein structures with no alpha carbons: draw a point cloud.
      if (!edges.length && !dots.length) {
        projectedAtoms.forEach(function(p){
          ctx.fillStyle="#6d8eac"; ctx.fillRect(p.x-1,p.y-1,2,2);
        });
      }
    }
    const controls={
      zoom:function(f){state.zoom=Math.max(0.12,Math.min(20,state.zoom*f));draw();},
      fit:function(){state.zoom=1;state.ax=0.23;state.ay=-0.42;draw();},
      resize:draw
    };
    canvas.addEventListener("pointerdown",function(e){
      state.down=true;state.x=e.clientX;state.y=e.clientY;
      canvas.setPointerCapture(e.pointerId);canvas.style.cursor="grabbing";
    });
    canvas.addEventListener("pointermove",function(e){
      if(!state.down)return;
      state.ay+=(e.clientX-state.x)*0.009;
      state.ax+=(e.clientY-state.y)*0.009;
      state.x=e.clientX;state.y=e.clientY;draw();
    });
    canvas.addEventListener("pointerup",function(){state.down=false;canvas.style.cursor="grab";});
    canvas.addEventListener("pointercancel",function(){state.down=false;canvas.style.cursor="grab";});
    canvas.addEventListener("wheel",function(e){
      e.preventDefault();controls.zoom(e.deltaY<0?1.15:1/1.15);
    },{passive:false});
    fallbackControls=controls;
    [zoomIn,zoomOut,fit].forEach(function(b){b.disabled=false;});
    status.className="fallback";
    status.textContent="Canvas 2D molecular preview (simplified "+styleSelect.value+
      "; "+reason+"). Drag to rotate, scroll to zoom.";
    retry.style.display="none";
    draw();
  }

  function loadLibrary(index) {
    if (window.$3Dmol && typeof window.$3Dmol.createViewer === "function") {
      return Promise.resolve();
    }
    if (index >= data.cdn_urls.length) {
      return Promise.reject(new Error("3Dmol.js could not load from either CDN"));
    }
    return new Promise(function(resolve, reject) {
      const tag = document.createElement("script");
      let settled = false;
      const timeout = setTimeout(function() { finish(false); }, 12000);
      function finish(ok) {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (ok) resolve();
        else {
          tag.remove();
          reject(new Error("CDN unavailable: " + data.cdn_urls[index]));
        }
      }
      tag.src = data.cdn_urls[index];
      tag.async = true;
      tag.onload = function() {
        finish(!!(window.$3Dmol &&
          typeof window.$3Dmol.createViewer === "function"));
      };
      tag.onerror = function() { finish(false); };
      document.head.appendChild(tag);
    }).catch(function() { return loadLibrary(index + 1); });
  }

  function resizeViewer() {
    if (fallbackControls) { fallbackControls.resize(); return; }
    if (!viewer || scene.clientWidth < 1) return;
    try { viewer.resize(); viewer.render(); }
    catch (error) { errorMessage(error); }
  }

  function applyProteinStyle() {
    if (!viewer || fallbackControls) return;
    data.models.forEach(function(item,index) {
      if (item.kind!=="protein") return;
      const selection={model:index,hetflag:false};
      const mode=styleSelect.value;
      if (mode==="cartoon") {
        viewer.setStyle(selection,{cartoon:{color:"#7b94ad"}});
      } else if (mode==="stick") {
        viewer.setStyle(selection,{stick:{colorscheme:"Jmol"}});
      } else if (mode==="sphere") {
        viewer.setStyle(selection,{sphere:{scale:0.75,colorscheme:"Jmol"}});
      } else {
        viewer.setStyle(selection,{line:{colorscheme:"Jmol"}});
      }
      viewer.addStyle({model:index,hetflag:true},
                      {stick:{colorscheme:"cyanCarbon"}});
    });
    if (data.focus_serials.length) {
      viewer.addStyle({model:0,serial:data.focus_serials},
                      {stick:{color:"#f59e0b"}});
    }
    viewer.render();
  }

  function initializeViewer() {
    fallbackControls = null;
    scene.querySelectorAll("canvas").forEach(function(node) { node.remove(); });
    viewer = window.$3Dmol.createViewer(scene, {backgroundColor:"#ffffff"});
    if (!viewer) throw new Error("3Dmol.js did not initialize the renderer");

    let atomCount = 0;
    data.models.forEach(function(item, index) {
      const model = viewer.addModel(item.data, item.format);
      const atoms = model && model.selectedAtoms ? model.selectedAtoms({}) : [];
      if (!atoms || atoms.length === 0) {
        throw new Error("No atoms parsed from " + item.kind + " structure");
      }
      atomCount += atoms.length;
      if (item.kind === "protein") {
        // The user's selected protein style is applied once all models load.
      } else {
        const color = item.kind === "reference" ? "greenCarbon" : "magentaCarbon";
        viewer.setStyle({model:index}, {stick:{colorscheme:color}});
      }
    });
    applyProteinStyle();
    if (data.focus_serials.length) {
      viewer.addStyle({model:0, serial:data.focus_serials},
                      {stick:{color:"#f59e0b"}});
      viewer.zoomTo({model:0, serial:data.focus_serials});
    } else {
      viewer.zoomTo();
    }
    if (data.box) {
      viewer.addBox({
        center:data.box.center,
        dimensions:data.box.dimensions,
        color:"#f59e0b",
        wireframe:true
      });
    }
    viewer.render();
    if (!atomCount) throw new Error("No coordinates were rendered");
    [zoomIn, zoomOut, fit].forEach(function(button) { button.disabled = false; });
    status.className = "ready";
    status.textContent = "Rendered " + atomCount + " atoms";
    retry.style.display = "none";
    requestAnimationFrame(resizeViewer);
  }

  function start() {
    [zoomIn, zoomOut, fit].forEach(function(button) { button.disabled = true; });
    retry.style.display = "none";
    if (!webGLAvailable()) {
      try { createCanvasFallback("WebGL unavailable"); }
      catch (error) { errorMessage(error); }
      return;
    }
    status.className = "";
    status.textContent = "Loading molecular viewer...";
    loadLibrary(0).then(function() {
      try { initializeViewer(); }
      catch (error) {
        try { createCanvasFallback("3D renderer failed"); }
        catch (fallbackError) { errorMessage(fallbackError); }
      }
    }).catch(function(error) {
      try { createCanvasFallback("3D library unavailable"); }
      catch (fallbackError) { errorMessage(fallbackError); }
    });
  }

  zoomIn.addEventListener("click", function() {
    if (fallbackControls) fallbackControls.zoom(1.2);
    else if (viewer) { viewer.zoom(1.2); viewer.render(); }
  });
  zoomOut.addEventListener("click", function() {
    if (fallbackControls) fallbackControls.zoom(0.8);
    else if (viewer) { viewer.zoom(0.8); viewer.render(); }
  });
  fit.addEventListener("click", function() {
    if (fallbackControls) fallbackControls.fit();
    else if (viewer) { viewer.zoomTo(); viewer.render(); }
  });
  retry.addEventListener("click", start);
  styleSelect.addEventListener("change",function() {
    if (fallbackControls) fallbackControls.resize();
    else if (viewer) applyProteinStyle();
  });

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(resizeViewer).observe(scene);
  } else {
    window.addEventListener("resize", resizeViewer);
  }
  start();
})();
</script>
</body>
</html>"""
    return (html.replace("VIEW_ID", widget_id)
                .replace("__PANDOC_VIEWER_DATA__", encoded))
