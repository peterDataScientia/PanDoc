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
#VIEW_ID_toolbar { display:flex; gap:6px; flex-wrap:wrap;
                   padding:5px 2px; align-items:center; }
#VIEW_ID_toolbar button { padding:5px 11px; border-radius:6px;
                          border:1px solid #cbd5e1; background:#f8fafc;
                          color:#26384b; cursor:pointer; font-size:13px; }
#VIEW_ID_toolbar button:disabled { opacity:.5; cursor:not-allowed; }
#VIEW_ID_retry { display:none; }
</style>
</head>
<body>
<div id="VIEW_ID_scene">
  <div id="VIEW_ID_status" role="status" aria-live="polite">Loading molecular viewer...</div>
</div>
<div id="VIEW_ID_toolbar">
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
  let viewer = null;

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
    if (!viewer || scene.clientWidth < 1) return;
    try { viewer.resize(); viewer.render(); }
    catch (error) { errorMessage(error); }
  }

  function initializeViewer() {
    if (!webGLAvailable()) {
      throw new Error("WebGL is unavailable or disabled in this browser");
    }
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
        viewer.setStyle({model:index}, {cartoon:{color:"#7b94ad"}});
        viewer.addStyle({model:index, hetflag:true},
                        {stick:{colorscheme:"cyanCarbon"}});
      } else {
        const color = item.kind === "reference" ? "greenCarbon" : "magentaCarbon";
        viewer.setStyle({model:index}, {stick:{colorscheme:color}});
      }
    });
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
    status.className = "";
    status.textContent = "Loading molecular viewer...";
    loadLibrary(0).then(function() {
      try { initializeViewer(); }
      catch (error) { errorMessage(error); }
    }).catch(errorMessage);
  }

  zoomIn.addEventListener("click", function() {
    if (viewer) { viewer.zoom(1.2); viewer.render(); }
  });
  zoomOut.addEventListener("click", function() {
    if (viewer) { viewer.zoom(0.8); viewer.render(); }
  });
  fit.addEventListener("click", function() {
    if (viewer) { viewer.zoomTo(); viewer.render(); }
  });
  retry.addEventListener("click", start);

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
