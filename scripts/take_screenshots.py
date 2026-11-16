"""Take the README screenshots of the running app.

It starts its own throwaway copy of the app (a temporary data file holding the demo owner, offline mode, localhost
only), drives it with a headless Chromium, and writes the images to screenshots/. It never touches your data.json and
never calls Gemini or the network.

    pip install playwright          # not a project requirement: only for this script
    playwright install chromium
    python scripts/take_screenshots.py

The window is 1100 px wide and the images are 1.5x, so text stays sharp. screenshots/ui_ai_assistant.png (a live
Gemini run) is not made here: it needs a key, so it is kept as it was captured.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots"
X, W = 56, 988  # the content column of a 1100 px window
SCALE = 1.5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(folder: Path, port: int) -> subprocess.Popen:
    sys.path.insert(0, str(ROOT))
    import main  # the demo owner the command-line demo and "Load sample data" use

    data = folder / "data.json"
    main.build_owner().save_to_json(data)
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "PAWPAL_OFFLINE")}
    env.update(OFFLINE_MODE="1", PAWPAL_NO_DOTENV="1", PAWPAL_DATA_FILE=str(data),
               PAWPAL_TRACE_FILE=str(folder / "trace.jsonl"))
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", str(port),
         "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).read() == b"ok":
                return server
        except OSError:
            time.sleep(0.5)
    server.terminate()
    raise SystemExit("the app did not start")


def settle(page, ms: int = 900) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def clip_between(page, top: dict, bottom: dict, name: str, pad: int = 12) -> None:
    y0 = max(0, top["y"] - pad)
    y1 = bottom["y"] + bottom["height"] + pad
    page.screenshot(path=str(OUT / name), clip={"x": X - 20, "y": y0, "width": W + 40, "height": y1 - y0})
    print(f"wrote screenshots/{name}")


def shoot(page, url: str) -> None:
    page.goto(url)
    page.wait_for_selector("text=PawPal+", timeout=30000)
    settle(page, 1500)
    page.get_by_role("tab", name="Settings").click()  # one rerun, so the one-time "Loaded ..." banner is gone
    settle(page)
    page.get_by_role("tab", name="Pets & tasks").click()
    settle(page)

    # The pets (with the card and the buttons that open the assistant) and the clash warning.
    caption = page.get_by_text("You cannot do two things at once").bounding_box()
    clip_between(page, {"y": 62, "height": 0}, caption, "ui_pets_and_conflicts.png", pad=16)

    # The free-slot panel, opened.
    page.locator("summary", has_text="Find a free slot").click()
    settle(page, 700)
    panel = page.locator("details", has_text="Find a free slot").bounding_box()
    clip_between(page, panel, panel, "ui_free_slot.png", pad=10)
    page.locator("summary", has_text="Find a free slot").click()
    settle(page, 400)

    # The task list (the first five tasks).
    header = page.get_by_text("Task list", exact=True).bounding_box()
    fifth_edit = page.locator("details", has_text="Edit '").nth(4).bounding_box()
    clip_between(page, header, fifth_edit, "ui_task_list.png", pad=10)

    # The daily schedule.
    page.get_by_role("tab", name="Daily schedule").click()
    settle(page)
    page.get_by_role("button", name="Generate schedule").click()
    settle(page, 1500)
    top = page.get_by_text("Daily schedule", exact=True).last.bounding_box()
    why = page.locator("details", has_text="Why this plan?").bounding_box()
    clip_between(page, top, why, "ui_schedule.png", pad=10)

    # The assistant tab: before any suggestion, and after one (offline).
    page.get_by_role("tab", name="AI Care Assistant").click()
    settle(page)
    how = page.locator("details", has_text="How it works").bounding_box()
    clip_between(page, {"y": 176, "height": 0}, how, "ui_ai_assistant_start.png", pad=10)
    page.get_by_role("button", name="Suggest tasks for Biscuit").click()
    settle(page, 2500)
    log = page.locator("details", has_text="Agent log for this run").bounding_box()
    clip_between(page, {"y": 176, "height": 0}, log, "ui_ai_assistant_offline.png", pad=10)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("This script needs Playwright: pip install playwright && playwright install chromium")
        return 1
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        port = free_port()
        server = start_server(Path(folder), port)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1100, "height": 2600}, device_scale_factor=SCALE)
                shoot(page, f"http://127.0.0.1:{port}")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
