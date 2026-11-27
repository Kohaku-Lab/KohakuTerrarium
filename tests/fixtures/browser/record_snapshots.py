"""Regenerate the recorded AI snapshots in ``snapshots/`` from the pages in ``site/``.

Run with an interpreter that has Playwright and Chromium:
``python tests/fixtures/browser/record_snapshots.py``. Not collected by pytest.
"""

import asyncio
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
OUT = HERE / "snapshots"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory: Path) -> ThreadingHTTPServer:
    """Serve ``directory`` on a free loopback port in a daemon thread."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def write(name: str, text: str) -> None:
    (OUT / f"{name}.txt").write_text(text + "\n", encoding="utf-8", newline="\n")


async def fresh_page(browser):
    """A page in a new context, so main-frame refs start at ``e1``."""
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    return await context.new_page()


async def record() -> None:
    main = serve(SITE)
    other = serve(SITE)
    base = f"http://127.0.0.1:{main.server_address[1]}"
    cross = f"http://127.0.0.1:{other.server_address[1]}/frame_inner.html"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for name in ("controls", "hidden", "edge"):
                page = await fresh_page(browser)
                await page.goto(f"{base}/{name}.html")
                write(name, await page.aria_snapshot(mode="ai"))
            page = await fresh_page(browser)
            await page.goto(f"{base}/frames.html?cross={cross}")
            await page.frame_locator("#cross").locator("button").wait_for()
            write("frames", await page.aria_snapshot(mode="ai"))

            page = await fresh_page(browser)
            await page.goto(f"{base}/controls.html")
            await page.aria_snapshot(mode="ai")
            await page.locator("#save").click()
            await page.locator("#news").uncheck()
            await page.locator("#name").fill("Grace")
            await page.locator("#color").select_option("Blue")
            write("controls_after", await page.aria_snapshot(mode="ai"))
        finally:
            await browser.close()
            main.shutdown()
            other.shutdown()


if __name__ == "__main__":
    asyncio.run(record())
