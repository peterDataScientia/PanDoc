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

            # Confirm the application exposes deterministic workflow and compute state.
            stage = marker(page, "pandoc-workflow-stage").get_attribute("data-state")
            node = marker(page, "pandoc-compute-node").get_attribute("data-state")
            assert stage == "1 · Load complex", stage
            assert node == "Computer A · Fast", node

            # Select the deposited ethanol component as the generic crystallographic reference.
            combo = page.get_by_role("combobox", name="Crystallographic reference ligand")
            combo.click()
            page.get_by_role("option", name=re.compile(r"EOH")).click()

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
