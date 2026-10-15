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

            # Workflow navigation lives ONLY in the collapsible sidebar.
            # The main page must start with a compact active-stage heading.
            assert page.locator(".pd-stepper").count() == 0
            assert page.get_by_role("button", name="☰ Workflow menu").count() == 0
            heading = page.locator(".pd-workspace-heading")
            heading.wait_for(state="visible")
            heading_box = heading.bounding_box()
            assert heading_box and 0 <= heading_box["y"] < 150, (
                "Excessive whitespace above STEP 1 OF 5: " + str(heading_box)
            )
            assert page.locator(".pd-workspace-title").inner_text() == "Load complex"

            # The standard hamburger opens sidebar navigation and settings.
            hamburger = page.get_by_test_id("stExpandSidebarButton")
            hamburger.wait_for(state="visible")
            hamburger.click()
            sidebar = page.get_by_test_id("stSidebar")
            sidebar.get_by_text("Workflow", exact=True).wait_for(state="visible")
            sidebar.get_by_role("button", name="2 · Prepare structures").click()
            wait_marker_state(page, "pandoc-workflow-stage", "2 · Prepare structures")
            assert page.locator(".pd-workspace-title").inner_text() == "Prepare structures"
            sidebar.get_by_role("button", name="1 · Load complex").click()
            wait_marker_state(page, "pandoc-workflow-stage", "1 · Load complex")
            assert page.locator(".pd-workspace-title").inner_text() == "Load complex"
            sidebar.get_by_text("Where calculations run", exact=True).wait_for(state="visible")

            # Buttons must be readable BEFORE hover. Regression for white-on-white
            # actions seen in the high-contrast theme.
            def style_snapshot(name):
                return page.get_by_role("button", name=name, exact=True).evaluate(
                    """el => {
                        const computed = getComputedStyle(el);
                        const label = el.querySelector('p, span');
                        return {
                            color:computed.color,
                            background:computed.backgroundColor,
                            gradient:computed.backgroundImage,
                            border:computed.borderColor,
                            labelColor:label ? getComputedStyle(label).color : computed.color,
                            opacity:Number(computed.opacity)
                        };
                    }"""
                )

            secondary = style_snapshot("Start a new experiment")
            primary = style_snapshot("Run 4-receptor batch review")
            assert secondary["background"] == "rgb(223, 235, 246)", secondary
            assert secondary["color"] == "rgb(16, 40, 63)", secondary
            assert secondary["labelColor"] == secondary["color"], secondary
            assert secondary["opacity"] >= 0.95, secondary
            assert primary["color"] == "rgb(255, 255, 255)", primary
            assert primary["labelColor"] == primary["color"], primary
            assert "gradient" in primary["gradient"], primary

            page.get_by_role("button", name="Start a new experiment").hover()
            secondary_hover = style_snapshot("Start a new experiment")
            assert secondary_hover["color"] == secondary["color"], secondary_hover
            assert secondary_hover["labelColor"] == secondary_hover["color"]

            # The user selects real compute providers; their stored values stay stable.
            # The user selects real providers; stored values remain legacy IDs.
            page.get_by_text("This app (default)", exact=True).click()
            page.get_by_role("radio", name="Kaggle worker").wait_for(state="visible")
            page.get_by_role("radio", name="GitHub Actions").wait_for(state="visible")

            page.get_by_text("Kaggle worker", exact=True).click()
            wait_marker_state(page, "pandoc-compute-node", "Computer B · High capacity")
            page.get_by_text("GitHub Actions", exact=True).click()
            wait_marker_state(page, "pandoc-compute-node", "Computer C · Backup")
            page.get_by_text("This app (default)", exact=True).click()
            wait_marker_state(page, "pandoc-compute-node", "Computer A · Fast")

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

            # Preparation is now a forward-flow workspace: pH is immediately
            # visible; advanced settings and the 3D receptor inspection are optional.
            # No jumping from the bottom of the receptor tab back to the top
            # to reach the crystallographic reference ligand.
            ph_control = page.get_by_role("spinbutton", name="Preparation pH")
            ph_control.wait_for(state="visible")
            # Allow asynchronous Streamlit content + one-shot scroll helper to settle.
            # Do NOT call scroll_into_view_if_needed here: that would conceal the bug.
            page.wait_for_timeout(1800)
            ph_box = ph_control.bounding_box()
            print("AUTO-SCROLL CHECK", ph_box, page.evaluate(
                """() => ({top:document.querySelector('[data-testid="stMain"]')?.scrollTop,
                        iframes:Array.from(document.querySelectorAll('iframe')).map(x=>({
                          srcdoc:(x.getAttribute('srcdoc')||'').slice(0,120)})).slice(0,5)})"""), flush=True)
            assert ph_box and 40 <= ph_box["y"] < 310, (
                "After saving selection, pH requires manual scrolling: " + str(ph_box)
            )
            page.get_by_role("button", name="Analyze protonation at selected pH").wait_for(state="visible")
            page.get_by_role("button", name="Prepare receptor").wait_for(state="visible")
            assert page.locator('[data-testid="stTabs"]').count() == 0
            # The ligand section is directly below the receptor; no tab or
            # secondary navigation click is needed.
            page.get_by_role("button", name="Find ligand chemistry from PDB").wait_for(state="visible")
            assert page.get_by_text("4. Reference ligand preparation", exact=False).count() >= 1
            page.get_by_text("Optional receptor settings", exact=True).click()
            page.get_by_text("Rebuild missing heavy atoms with PDBFixer", exact=True).click()
            wait_marker_state(page, "pandoc-workflow-stage", "2 · Prepare structures")
            assert not page.get_by_role("checkbox", name="Rebuild missing heavy atoms with PDBFixer").is_checked()
            page.wait_for_timeout(550)
            print("AFTER REPAIR CLICK", page.evaluate("""() => ({
                stage:document.querySelector('[data-testid="pandoc-workflow-stage"]')?.getAttribute('data-state'),
                title:document.querySelector('.pd-workspace-title')?.textContent,
                errors:Array.from(document.querySelectorAll('[data-testid="stException"]'))
                    .map(x=>x.textContent?.slice(0,350)),
                ligandFound:document.body.innerText.includes("Find ligand chemistry from PDB"),
                ligandSection:document.body.innerText.includes("Reference ligand preparation"),
                tail:document.body.innerText.slice(-700)
            })"""), flush=True)
            page.get_by_role("button", name="Find ligand chemistry from PDB").wait_for(state="visible")

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

            # The workflow stays in the sidebar after upload and rerun.
            assert sidebar.get_by_role("button", name="2 · Prepare structures").is_visible()
            assert page.get_by_role("button", name="☰ Workflow menu").count() == 0
            assert page.locator(".pd-workspace-title").inner_text() == "Prepare structures"

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
