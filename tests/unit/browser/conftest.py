"""Fixtures for the browser unit tests: a synchronous Chromium page for probe tests.

Browser-backed tests skip cleanly when Playwright or its Chromium is missing.
"""

import os

import pytest

from tests.helpers.browser_site import browsers_env


@pytest.fixture
def chromium_page(monkeypatch):
    """A blank 1280x800 page in a headless Chromium, closed after the test."""
    sync_api = pytest.importorskip("playwright.sync_api")
    for name, value in browsers_env().items():
        monkeypatch.setenv(name, value)
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("KT_CHROMIUM_PATH") or None
            )
        except Exception as exc:
            pytest.skip(f"no Chromium for Playwright: {exc}")
        try:
            yield browser.new_page(viewport={"width": 1280, "height": 800})
        finally:
            browser.close()
