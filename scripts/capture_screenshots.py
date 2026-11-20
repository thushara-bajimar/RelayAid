"""Drive the real dashboard with a headless browser and save screenshots to docs/screenshots.

Requires: cluster running (scripts/run_cluster.py --seed) and `npm run dev` in frontend/.
Uses the locally installed Microsoft Edge through Playwright (no browser download).

    python scripts/capture_screenshots.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
URL = os.environ.get("RELAYAID_UI", "http://127.0.0.1:5173")
PASS = os.environ.get("RELAYAID_OPERATOR_PASSPHRASE", "relayaid-demo")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("PW_CHANNEL", "msedge"))
        page = browser.new_page(viewport={"width": 1500, "height": 950}, device_scale_factor=1)
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(URL)
        page.screenshot(path=str(OUT / "00-login.png"))
        page.fill("input[type=password]", PASS)
        page.click("button[type=submit]")
        page.wait_for_selector("text=Coordination overview")
        page.wait_for_timeout(2500)

        def shot(hash_: str, name: str, wait="body", full=True, delay=2200):
            page.goto(f"{URL}/#{hash_}")
            page.wait_for_selector(wait)
            page.wait_for_timeout(delay)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        # scenario runs first so conflicts / allocations exist for the later screenshots
        page.goto(f"{URL}/#lab")
        page.wait_for_selector("text=Guided scenarios")
        page.wait_for_timeout(1500)
        for label in ("3 · Double allocation attempt", "2 · Conflicting edits", "4 · Repeated partitions",
                      "1 · Independent offline updates"):
            page.get_by_role("button", name=label).click()
            page.wait_for_function("document.querySelector('pre').innerText.trim().endsWith('done')", timeout=60000)
            page.wait_for_timeout(800)
            if label.startswith("3"):
                page.screenshot(path=str(OUT / "09-lab-scenario3-double-allocation.png"), full_page=True)
                print("saved scenario 3")
            if label.startswith("2"):
                page.screenshot(path=str(OUT / "10-lab-scenario2-conflicts.png"), full_page=True)
                print("saved scenario 2")
        # show a live partition in the topology
        page.get_by_role("button", name="Isolate Charlie").click()
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "08-partition-lab.png"), full_page=True)
        page.get_by_role("button", name="Heal all").click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Sync until converged").click()
        page.wait_for_selector("text=Converged", timeout=60000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "11-lab-converged.png"), full_page=True)
        for n in ("alpha", "bravo", "charlie"):  # re-enable gossip after the guided runs
            page.evaluate(f"""fetch('/n/{n}/api/sim', {{method:'PUT', headers:{{'Content-Type':'application/json',
                Authorization:'Bearer '+JSON.parse(sessionStorage.getItem('relayaid.sessions')).{n}.token}},
                body: JSON.stringify({{auto_sync:true}})}})""")

        shot("overview", "01-overview", "text=Coordination overview")
        shot("needs", "02-needs", "text=Emergency needs")
        shot("supplies", "03-supplies-allocation", "text=Supplies & allocation rights")
        shot("shelters", "04-shelters", "text=Shelters")
        shot("tasks", "05-tasks", "text=Tasks & teams")
        shot("people", "06-people-protected", "text=People - missing & found")
        shot("conflicts", "07-conflict-center", "text=Conflict center")
        shot("sync", "12-sync-center", "text=Sync center")
        shot("nodes", "13-nodes", "text=Node registry")
        shot("analytics", "14-analytics", "text=Per-node comparison")

        # blocked allocation dialog on Bravo for Alpha's boat
        page.goto(f"{URL}/#supplies")
        page.get_by_role("tab", name="Bravo").click()
        page.wait_for_timeout(2000)
        card = page.locator("section", has_text="Rescue boat R-01").first
        card.get_by_role("button", name="Allocate").first.click()
        page.get_by_role("dialog").or_(page.locator("text=Requires the exclusive token")).first.wait_for()
        page.locator("div.fixed button", has_text="Allocate").last.click()
        page.wait_for_selector("text=Allocation blocked.")
        page.screenshot(path=str(OUT / "15-allocation-blocked.png"))
        print("saved blocked allocation")

        # a responder sees redacted people records
        page.evaluate("sessionStorage.clear()")
        page.goto(URL)
        page.select_option("select", "responder")
        page.fill("input[type=password]", PASS)
        page.click("button[type=submit]")
        page.wait_for_selector("text=Coordination overview")
        shot("people", "16-people-responder-redacted", "text=People - missing & found")
        browser.close()

    if errors:
        print("\nbrowser errors:")
        for e in errors:
            print(" ", e)
        sys.exit(1)
    print("no browser errors")


if __name__ == "__main__":
    main()
