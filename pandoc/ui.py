from __future__ import annotations

import html
from pathlib import Path

STAGES = [
    "Load complex",
    "Prepare structures",
    "Validate docking",
    "Run experiment",
    "Explore results",
]

def load_css(st, path):
    path = Path(path)
    if path.exists():
        st.markdown(f"<style>{path.read_text()}</style>", unsafe_allow_html=True)

def badge(label, kind="neutral"):
    return f'<span class="pd-badge {html.escape(kind)}">{html.escape(str(label))}</span>'

def tooltip(label, text):
    return (
        f'<span class="pd-tooltip">{html.escape(str(label))}'
        f'<span class="pd-tooltip-text">{html.escape(str(text))}</span></span>'
    )

def card(st, title, body, *, badge_text=None, badge_kind="neutral", meta=None):
    right = badge(badge_text, badge_kind) if badge_text else ""
    meta_html = f'<div class="pd-card-meta">{html.escape(str(meta))}</div>' if meta else ""
    st.markdown(
        f'''<div class="pd-card">
              <div class="pd-card-title"><span>{html.escape(str(title))}</span>{right}</div>
              <div class="pd-card-body">{body}</div>{meta_html}
            </div>''',
        unsafe_allow_html=True,
    )

def callout(st, text, kind="info"):
    st.markdown(
        f'<div class="pd-callout {html.escape(kind)}">{html.escape(str(text))}</div>',
        unsafe_allow_html=True,
    )

def shell_header(st, title, subtitle, kicker="PanDoc docking workbench"):
    st.markdown(
        f'''<div class="pd-shell-header">
              <div class="pd-shell-kicker">{html.escape(kicker)}</div>
              <div class="pd-shell-title">{html.escape(str(title))}</div>
              <div class="pd-shell-subtitle">{html.escape(str(subtitle))}</div>
            </div>''',
        unsafe_allow_html=True,
    )

def scroll_anchor(st, name):
    """Give a workflow action a stable target, independent of Streamlit widget IDs."""
    safe = str(name)
    if not safe.startswith("pd-") or not safe.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid workflow scroll target")
    st.markdown(
        f'<div id="{html.escape(safe, quote=True)}" class="pd-scroll-anchor" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )


def auto_scroll(st, name):
    """Reveal an application-owned anchor after deliberate workflow navigation.

    Streamlit >=1.52 allows JavaScript in st.html without a lazy-loaded iframe.
    Do not run on ordinary widget changes: only explicit stage transitions or
    completed scientific actions call this helper. Only hardcoded IDs are accepted.
    """
    import json

    allowed = {"pd-ph", "pd-protonation", "pd-ligand",
               "pd-receptor-ready", "pd-reference-ready", "pd-stage"}
    safe = str(name)
    if safe not in allowed:
        return
    target_id = json.dumps(safe)
    st.html(
        """<script>
        (function() {
          const id = """ + target_id + """;
          let attempts = 0;
          function reveal() {
            const node = document.getElementById(id);
            if (node) {
              node.scrollIntoView({behavior:"instant",block:"start",inline:"nearest"});
              document.documentElement.dataset.pandocScrollTarget = id;
            }
            // Let Streamlit finish replacing DOM and restoring its previous
            // scroll position. Reapply briefly after the new content mounts.
            if (++attempts < 14) setTimeout(reveal, 140);
          }
          requestAnimationFrame(reveal);
        })();
        </script>""",
        unsafe_allow_javascript=True,
    )


def glossary(st, items):
    chips = []
    for label, description in items:
        chips.append(tooltip(label, description))
    st.markdown(
        '<div class="pd-card"><div class="pd-card-title"><span>Scientific terms</span>'
        + badge('Hover for definitions', 'neutral')
        + '</div><div class="pd-card-body" style="display:flex;gap:16px;flex-wrap:wrap">'
        + ' · '.join(chips)
        + '</div></div>',
        unsafe_allow_html=True,
    )


def automation_marker(st, name, *, state=None, value=None, text=None):
    """Expose a stable, framework-independent DOM marker for browser automation.

    External clients should prefer these data-testid markers for application
    state, and accessible role/name selectors for Streamlit controls.
    """
    attrs = [f'data-testid="pandoc-{html.escape(str(name), quote=True)}"']
    if state is not None:
        attrs.append(f'data-state="{html.escape(str(state), quote=True)}"')
    if value is not None:
        attrs.append(f'data-value="{html.escape(str(value), quote=True)}"')
    label = text if text is not None else (state if state is not None else value)
    aria = html.escape(str(label or name), quote=True)
    st.markdown(
        '<div class="pd-automation-marker" role="status" aria-live="polite" '
        + ' '.join(attrs)
        + f' aria-label="{aria}"></div>',
        unsafe_allow_html=True,
    )


def automation_snapshot(st, *, stage, compute_node, complex_loaded, receptor_ready, reference_ready):
    """Emit deterministic workflow-state markers used by Playwright-style clients."""
    automation_marker(st, 'app-ready', state='ready')
    automation_marker(st, 'workflow-stage', state=str(stage))
    automation_marker(st, 'compute-node', state=str(compute_node))
    automation_marker(st, 'complex', state='ready' if complex_loaded else 'pending')
    automation_marker(st, 'receptor', state='ready' if receptor_ready else 'pending')
    automation_marker(st, 'reference', state='ready' if reference_ready else 'pending')
