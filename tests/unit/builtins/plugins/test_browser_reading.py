"""Unit tests for ``builtins.plugins.browser.reading``: a page read under every framework gate."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.browser.errors import (
    BrowserUnavailableError,
    NavigationBlockedError,
)
from kohakuterrarium.browser.launch import Candidate
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.protocol import NavResponse
from kohakuterrarium.browser.read import read_page
from kohakuterrarium.builtins.plugins.browser.reading import read_page_for
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels.channels import TERRARIUM_ENGINE_KEY
from tests.helpers.browser_driver import ScriptedDownload, ScriptedDriver
from tests.helpers.browser_plugin import call, plugin_context, scripted_plugin

URL = "http://shop.test/page"
FAST = {"action_timeout_s": 0.3, "nav_timeout_s": 0.5}


def page_setup(browser) -> None:
    browser.site[URL] = {
        "title_text": "Shop page",
        "html_text": "<html><body>Seats: 5</body></html>",
        "body_text": "Seats: 5",
    }


def context(tmp_path, *, sandbox=None, host_resources=True, services=None):
    """A tool context with ``sandbox``'s services, in an engine with ``host_resources``."""
    environment = {TERRARIUM_ENGINE_KEY: SimpleNamespace(host_resources=host_resources)}
    tool = ToolContext(
        agent_name="reader", session=None, working_dir=tmp_path, environment=environment
    )
    if sandbox is not None:
        tool.runtime_services.update(
            SandboxPlugin(options=sandbox).runtime_services(None)
        )
    tool.runtime_services.update(services or {})
    return tool


def options(**values) -> BrowserOptions:
    return BrowserOptions.from_mapping({**FAST, **values})


async def test_an_allowed_read_returns_the_page_and_leaves_no_browser(tmp_path):
    driver = ScriptedDriver(page_setup)
    page = await read_page_for(context(tmp_path), URL, options(), driver=driver)
    assert (page.url, page.title, page.text) == (URL, "Shop page", "Seats: 5")
    assert len(driver.plans) == 1 and driver.alive() == []
    assert [c.label for c in driver.plans[0].candidates][0] == "bundled chromium"
    assert len(driver.plans[0].candidates) > 1, "unrestricted: system browsers too"


async def test_the_sandbox_network_rules_refuse_before_any_launch(tmp_path):
    driver = ScriptedDriver(page_setup)
    tool = context(tmp_path, sandbox={"network_allowlist": ["docs.test"]})
    with pytest.raises(NavigationBlockedError, match="not allowlisted"):
        await read_page_for(tool, URL, options(), driver=driver)
    assert driver.plans == []
    denied = context(tmp_path, sandbox={"network": "deny"})
    with pytest.raises(NavigationBlockedError):
        await read_page_for(denied, URL, options(), driver=driver)
    assert driver.plans == []
    allowed = context(tmp_path, sandbox={"network_allowlist": ["shop.test"]})
    page = await read_page_for(allowed, URL, options(), driver=driver)
    assert page.text == "Seats: 5" and driver.alive() == []


async def test_required_os_confinement_refuses_before_any_launch(tmp_path):
    driver = ScriptedDriver(page_setup)
    tool = context(tmp_path, sandbox={"os_enforce": "require"})
    with pytest.raises(BrowserUnavailableError, match="os_enforce"):
        await read_page_for(tool, URL, options(), driver=driver)
    assert driver.plans == []


async def test_the_low_level_read_applies_only_the_check_it_is_given(tmp_path):
    driver = ScriptedDriver(page_setup)
    tool = context(tmp_path, sandbox={"os_enforce": "require"}, host_resources=False)
    policy = tool.runtime_services["network_policy"]
    page = await read_page(
        URL, options(allow_loopback=True), external_check=policy.check, driver=driver
    )
    assert page.title == "Shop page", "no confinement or host-resource gate here"
    assert len(driver.plans[0].candidates) > 1


async def test_a_creature_without_host_resources_gets_only_the_bundled_chromium(
    tmp_path,
):
    driver = ScriptedDriver(page_setup)
    fenced = context(tmp_path, host_resources=False)
    with pytest.raises(BrowserUnavailableError) as info:
        await read_page_for(
            fenced, URL, options(allow_loopback=True, headless=False), driver=driver
        )
    assert "refused options: headless: false, allow_loopback" in str(info.value)
    assert driver.plans == []
    page = await read_page_for(fenced, URL, options(), driver=driver)
    assert page.title == "Shop page"
    assert driver.plans[0].candidates == (Candidate("bundled chromium"),)


async def test_downloads_the_page_starts_are_never_written(tmp_path):
    item = ScriptedDownload("report.csv", b"1")

    def setup(browser) -> None:
        page_setup(browser)
        browser.page_setup = lambda page: page.handlers.update(
            {"goto": lambda **_: _load_and_download(browser, page, item)}
        )

    driver = ScriptedDriver(setup)
    tool = context(tmp_path, sandbox={"fs_write": "workspace"})
    await read_page_for(tool, URL, options(), driver=driver)
    assert item.cancelled and item.saved_to is None
    assert list(tmp_path.iterdir()) == []


def _load_and_download(browser, page, item):
    page.load(URL)
    browser.sink.download_started(page, item)
    return NavResponse(URL, 200)


async def test_the_gates_are_the_same_with_the_plugin_loaded_and_its_browser_untouched(
    tmp_path,
):
    plugin, plugin_driver = scripted_plugin()
    await plugin.on_load(plugin_context(tmp_path))
    try:
        services = plugin.runtime_services(None)
        tool = context(tmp_path, sandbox={"os_enforce": "require"}, services=services)
        driver = ScriptedDriver(page_setup)
        with pytest.raises(BrowserUnavailableError, match="os_enforce"):
            await read_page_for(tool, URL, options(), driver=driver)
        free = context(tmp_path, services=services)
        assert (await call(plugin, "browser_snapshot", {}, free)).success
        page = await read_page_for(free, URL, options(), driver=driver)
        assert page.title == "Shop page" and driver.alive() == []
        assert len(plugin_driver.plans) == 1
        assert [tab.id for tab in plugin.session._tabs] == ["t1"]
    finally:
        await plugin.on_unload()
    assert plugin_driver.alive() == []
