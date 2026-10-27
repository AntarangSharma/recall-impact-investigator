from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.browser
def test_three_row_clarification_browser_restart(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "RECALL_RUNTIME": str(tmp_path / "runtime")}

    def start() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "recall.web:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(
                    process.stderr.read().decode() if process.stderr else "Server failed"
                )
            try:
                if httpx.get(url + "/health").status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        process.terminate()
        raise AssertionError("Server did not start")

    server = start()
    capture = os.getenv("RECALL_SCREENSHOTS")
    screenshots = Path(capture) if capture else tmp_path / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
            page.goto(url)
            expect(page.get_by_text("No investigations yet.", exact=False)).to_be_visible()
            page.screenshot(path=str(screenshots / "home.png"), full_page=True)
            page.get_by_role("button", name="Open fixture demo").click()
            page.wait_for_url("**/investigations/*")
            identity = page.url.rsplit("/", 1)[1]
            expect(page.locator(".stock-row")).to_have_count(3)
            expect(page.locator('[data-stock="SEC-001"] .stock-top .status')).to_have_text(
                "affected by notice"
            )
            expect(page.locator('[data-stock="SEC-002"] .stock-top .status')).to_have_text(
                "excluded by notice"
            )
            expect(page.locator('[data-stock="SEC-003"] .stock-top .status')).to_have_text(
                "needs information"
            )
            expect(page.get_by_label("What is the date code", exact=False)).to_be_visible()
            page.screenshot(path=str(screenshots / "clarification.png"), full_page=True)
            server.terminate()
            server.wait(timeout=10)
            server = start()
            page.reload()
            expect(page.get_by_label("What is the date code", exact=False)).to_be_visible()
            page.get_by_label("What is the date code", exact=False).fill("1901")
            page.get_by_role("button", name="Submit identifier and resume").click()
            expect(page.locator(".run-state")).to_have_text("Resumed / complete")
            expect(page.locator('[data-stock="SEC-003"] .stock-top .status')).to_have_text(
                "affected by notice"
            )
            page.locator('[data-stock="SEC-003"] summary').click()
            expect(page.locator('[data-stock="SEC-003"] blockquote')).to_contain_text(
                "SAF-53D (TXG-DT16E) with date code 1901"
            )
            page.screenshot(path=str(screenshots / "resumed-evidence.png"), full_page=True)
            export = page.request.get(url + f"/investigations/{identity}/export/findings.csv")
            assert export.status == 200 and len(export.text().splitlines()) == 10
            body = page.request.get(url + f"/api/investigations/{identity}").json()
            assert body["investigation"]["resumed"] == 1
            assert len(body["rows"]) == 3 and not body["requests"]
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(url + f"/investigations/{identity}")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
