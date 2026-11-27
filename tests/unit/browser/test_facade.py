"""Tests for the public facade ``kohakuterrarium.browser``: names, laziness, no Playwright."""

import json
import subprocess
import sys

import pytest

import kohakuterrarium.browser as browser
from kohakuterrarium.browser import access, errors, options, read
from kohakuterrarium.browser import session as session_package
from kohakuterrarium.browser.session import reading

EXPECTED = {
    "BROWSER_SERVICE": access,
    "BrowserAccess": access,
    "agent_session": access,
    "browser_access": access,
    "BrowserOptions": options,
    "BrowserResult": session_package,
    "BrowserSession": session_package,
    "PageRead": reading,
    "read_page": read,
    "BrowserError": errors,
    "BrowserUnavailableError": errors,
    "BrowserLaunchError": errors,
    "BrowserClosedError": errors,
    "BrowserTimeoutError": errors,
    "BrowserStateLostError": errors,
    "NavigationBlockedError": errors,
    "NavigationError": errors,
    "PageNavigatingError": errors,
}

PROBE = """
import json, sys
import kohakuterrarium.browser as browser
loaded = sorted(m for m in sys.modules if m.startswith(("playwright", "kohakuterrarium.browser.")))
for name in browser.__all__:
    getattr(browser, name)
after = sorted(m for m in sys.modules if m.startswith("playwright"))
print(json.dumps({"loaded": loaded, "after": after}))
"""


def test_every_public_name_is_the_object_its_module_defines():
    assert sorted(browser.__all__) == sorted(EXPECTED)
    for name, module in EXPECTED.items():
        assert getattr(browser, name) is getattr(module, name), name


def test_dir_lists_the_public_names():
    assert set(EXPECTED) <= set(dir(browser))


def test_an_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'Nope'"):
        browser.Nope  # noqa: B018


def test_importing_the_package_loads_nothing_and_using_it_never_loads_playwright():
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["loaded"] == []
    assert report["after"] == []
