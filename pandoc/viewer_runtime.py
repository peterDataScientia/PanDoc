"""Resilient HTML mounting for PanDoc's existing py3Dmol scene.

Keep all molecular models, chain styles and camera commands in the unchanged
py3Dmol object. Only replace the fragile one-source JavaScript loader and make
script/rendering failures visible instead of silently leaving a white iframe.
"""

from __future__ import annotations


# py3Dmol 2.5.x emits the same loader and outer promise wrapper. Fail loudly
# during integration tests if its template changes instead of silently removing
# scientific scene commands.
_LOADER_BEGIN = "var loadScriptAsync = function(uri){"
_LOADER_END = "if(typeof $3Dmolpromise === 'undefined') {"
_SCENE_END = "});\n</script>"

_RESILIENT_LOADER = r"""
var loadScriptAsync = function(uri) {
  // Fallbacks matter on desktop networks where one CDN is blocked.
  var urls = [
    uri,
    "https://3Dmol.org/build/3Dmol-min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.5.5/3Dmol-min.js"
  ];
  function attempt(index) {
    if (window.$3Dmol && typeof window.$3Dmol.createViewer === "function") {
      return Promise.resolve();
    }
    if (index >= urls.length) {
      return Promise.reject(new Error("3Dmol.js could not load from the available sources."));
    }
    return new Promise(function(resolve, reject) {
      var script = document.createElement("script");
      var finished = false;
      var timer = null;
      function finish(error) {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        if (error) {
          script.remove();
          reject(error);
        } else {
          resolve();
        }
      }
      script.async = true;
      script.src = urls[index];
      script.onload = function() {
        if (window.$3Dmol && typeof window.$3Dmol.createViewer === "function") {
          finish(null);
        } else {
          finish(new Error("3Dmol.js did not initialize from " + urls[index]));
        }
      };
      script.onerror = function() {
        finish(new Error("Could not download 3Dmol.js from " + urls[index]));
      };
      timer = setTimeout(function() {
        finish(new Error("Timed out loading 3Dmol.js from " + urls[index]));
      }, 9000);
      document.head.appendChild(script);
    }).catch(function() { return attempt(index + 1); });
  }
  return attempt(0);
};

"""


def render_viewer_html(view, *, controls="", legend=""):
    """Return responsive, diagnostic HTML without changing py3Dmol's scene."""
    raw = view._make_html()
    unique_id = str(view.uniqueid)
    if not unique_id.isdigit():
        raise ValueError("Unexpected py3Dmol viewer identifier.")

    start = raw.find(_LOADER_BEGIN)
    stop = raw.find(_LOADER_END, start)
    if start < 0 or stop < start:
        raise RuntimeError("The py3Dmol loader template changed; viewer integration needs review.")
    raw = raw[:start] + _RESILIENT_LOADER + raw[stop:]

    closing = raw.rfind(_SCENE_END)
    if closing < 0:
        raise RuntimeError("The py3Dmol render template changed; viewer integration needs review.")

    # Catch errors in both the script download and the scene/model/render path.
    # The old py3Dmol warning is removed before loading, so without this UI a
    # rejected promise results in a white empty viewer on some desktops.
    completion = """
}).then(function() {
  var node = document.getElementById("3dmolviewer_%s");
  var molecularView = window["viewer_%s"];
  if (!molecularView) throw new Error("3D viewer could not initialize.");
  if (node && typeof ResizeObserver !== "undefined") {
    var observer = new ResizeObserver(function() {
      if (!node.isConnected) { observer.disconnect(); return; }
      try { molecularView.resize(); molecularView.render(); } catch (ignored) {}
    });
    observer.observe(node);
  }
}).catch(function(error) {
  var warning = document.getElementById("pandoc-viewer-warning-%s");
  if (warning) {
    warning.style.display = "block";
    warning.textContent = "3D view unavailable: " +
      (error && error.message ? error.message : "JavaScript or WebGL failed.") +
      " Check browser Console (F12) or try another network.";
  }
});
</script>""" % (unique_id, unique_id, unique_id)
    raw = raw[:closing] + completion + raw[closing + len(_SCENE_END):]

    warning = (
        '<div id="pandoc-viewer-warning-%s" role="alert" '
        'style="display:none;background:#fff4ee;border:1px solid #f3b49c;'
        'padding:10px;border-radius:8px;color:#853b1c;font:13px Arial">'
        '</div>' % unique_id
    )
    return (
        '<style>html,body{margin:0;padding:0;background:white;overflow-x:hidden}'
        '[id^="3dmolviewer_"]{width:100%!important;min-width:0}</style>'
        + raw + controls + legend + warning
    )
