from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("PANDOC_E2E_URL", "http://127.0.0.1:8501")
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixture_complex.pdb")
ARTIFACT_DIR = ROOT / "test-artifacts" / "browser-contract"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def marker(page, testid: str):
    return page.locator(f'[data-testid="{testid}"]')


def wait_marker_state(page, testid: str, state: str, timeout: int = 60_000):
    loc = marker(page, testid)
    loc.wait_for(state="attached", timeout=timeout)
    page.wait_for_function(
        """([selector, expected]) => {
            const el = document.querySelector(selector);
            return !!el && el.getAttribute('data-state') === expected;
        }""",
        arg=[f'[data-testid="{testid}"]', state],
        timeout=timeout,
    )
    return loc


def main() -> int:
    if not FIXTURE.is_file():
        raise RuntimeError(f"Missing fixture: {FIXTURE}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_default_timeout(30_000)
        # Simulate the reported browser environment: WebGL blocked, Canvas 2D available.
        page.add_init_script("""
            (() => {
              const original = HTMLCanvasElement.prototype.getContext;
              HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
                if (kind === 'webgl' || kind === 'webgl2' ||
                    kind === 'experimental-webgl') return null;
                return original.call(this, kind, ...args);
              };
            })();
        """)

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded")
            wait_marker_state(page, "pandoc-app-ready", "ready")

            # The external client uses accessible names for controls.
            page.get_by_role("radio", name=re.compile(r"1 .* Load complex")).check()
            page.get_by_role("radio", name="Computer A · Fast").check()

            # Upload through the user-facing accessible label. Do not depend on
            # Streamlit-generated CSS classes, framework data-testid values, or DOM position.
            page.get_by_label("PDB or mmCIF complex", exact=True).locator('input[type="file"]').set_input_files(str(FIXTURE))
            wait_marker_state(page, "pandoc-complex", "ready")

            # A loaded PDB must produce a nonblank molecular view even if WebGL is off.
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll('iframe')).some(frame => {
                    try {
                        const canvas = frame.contentDocument &&
                          frame.contentDocument.querySelector('canvas[data-pandoc-renderer="2d"]');
                        if (!canvas || canvas.width < 1 || canvas.height < 1) return false;
                        const pixels = canvas.getContext('2d').getImageData(
                            0, 0, canvas.width, canvas.height).data;
                        for (let i = 0; i < pixels.length; i += 4) {
                            if (pixels[i] < 210 && pixels[i+1] < 210 &&
                                pixels[i+2] < 210 && pixels[i+3] > 0) return true;
                        }
                        return false;
                    } catch (_) { return false; }
                })""",
                timeout=30_000,
            )

            # The no-WebGL viewer starts as Cartoon and users can switch styles.
            # Compare actual rendered pixels rather than just checking the controls.
            assert page.evaluate("""() => {
              const iframe = Array.from(document.querySelectorAll('iframe')).find(f => {
                try { return !!f.contentDocument.querySelector(
                  'select[aria-label="Protein display style"]'); }
                catch (_) { return false; }
              });
              if (!iframe) return false;
              const doc = iframe.contentDocument;
              const style = doc.querySelector('select[aria-label="Protein display style"]');
              const canvas = doc.querySelector('canvas[data-pandoc-renderer="2d"]');
              if (!style || !canvas || style.value !== 'cartoon') return false;
              const before = canvas.toDataURL();
              for (const mode of ['stick', 'sphere', 'line']) {
                style.value = mode;
                style.dispatchEvent(new Event('change', {bubbles:true}));
                if (!canvas.width || !canvas.height) return false;
              }
              style.value = 'sphere';
              style.dispatchEvent(new Event('change', {bubbles:true}));
              const changed = canvas.toDataURL() !== before;
              style.value = 'cartoon';
              style.dispatchEvent(new Event('change', {bubbles:true}));
              return changed && style.value === 'cartoon';
            }"""), "Protein display selector did not update rendered molecular style"

            # Confirm the application exposes deterministic workflow and compute state.
            stage = marker(page, "pandoc-workflow-stage").get_attribute("data-state")
            node = marker(page, "pandoc-compute-node").get_attribute("data-state")
            assert stage == "1 · Load complex", stage
            assert node == "Computer A · Fast", node

            # Select the deposited ethanol component as the generic crystallographic reference.
            combo = page.get_by_role("combobox", name="Crystallographic reference ligand")
            # Streamlit 1.64 uses a searchable combobox whose choices may
            # render in a detached portal rather than a stable ARIA option.
            # Drive it with the accessible keyboard interaction instead.
            combo.click()
            combo.press("ArrowDown")
            combo.press("Enter")
            assert "EOH" in combo.input_value(), (
                "Expected reference ligand EOH, got " + combo.input_value()
            )

            page.get_by_role("button", name="Use selection and continue").click()

            # The app should move deterministically to structure preparation after rerun.
            wait_marker_state(page, "pandoc-complex", "ready")
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('[data-testid="pandoc-workflow-stage"]');
                    return !!el && (el.getAttribute('data-state') || '').startsWith('2 ·');
                }""",
                timeout=60_000,
            )

            # Contract markers must remain present after the Streamlit rerun.
            for testid in (
                "pandoc-app-ready",
                "pandoc-workflow-stage",
                "pandoc-compute-node",
                "pandoc-complex",
                "pandoc-receptor",
                "pandoc-reference",
            ):
                marker(page, testid).wait_for(state="attached")

            page.screenshot(path=str(ARTIFACT_DIR / "contract-pass.png"), full_page=True)
            print("PASS: PanDoc browser-automation contract survived upload, selection and rerun.")
            return 0
        except Exception:
            page.screenshot(path=str(ARTIFACT_DIR / "contract-failure.png"), full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
