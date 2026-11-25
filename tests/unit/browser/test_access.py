"""Tests for ``kohakuterrarium.browser.access``: how a tool reaches the agent's browser."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.browser.access import (
    BROWSER_SERVICE,
    BrowserAccess,
    agent_session,
    browser_access,
)
from kohakuterrarium.browser.errors import BrowserUnavailableError
from kohakuterrarium.builtins.plugins.browser.plugin import (
    BrowserPlugin,
    BrowserService,
)
from kohakuterrarium.builtins.tools.browser.navigate import BrowserNavigateTool


class Access:
    """A published access point that records the contexts it prepared for."""

    def __init__(self, session=None, error=None):
        self.session = session
        self.error = error
        self.prepared = []

    async def prepare(self, context):
        self.prepared.append(context)
        if self.error is not None:
            raise self.error
        return self.session


def context_with(services):
    return SimpleNamespace(runtime_services=services)


class TestBrowserAccess:
    def test_the_plugin_publishes_its_service_under_the_key(self, tmp_path):
        plugin = BrowserPlugin()
        plugin._accepting = True
        services = plugin.runtime_services(None)
        assert BROWSER_SERVICE == "browser"
        assert list(services) == [BROWSER_SERVICE]
        assert isinstance(services[BROWSER_SERVICE], BrowserService)
        assert isinstance(services[BROWSER_SERVICE], BrowserAccess)

    @pytest.mark.parametrize(
        "context",
        [
            None,
            SimpleNamespace(),
            context_with(None),
            context_with({}),
            context_with({"network_policy": object()}),
            context_with({BROWSER_SERVICE: None}),
        ],
    )
    async def test_no_published_browser_reads_as_none(self, context):
        assert browser_access(context) is None
        assert await agent_session(context) is None

    async def test_a_published_browser_is_prepared_for_the_calling_context(self):
        session = object()
        access = Access(session)
        context = context_with({BROWSER_SERVICE: access})
        assert browser_access(context) is access
        assert await agent_session(context) is session
        assert access.prepared == [context]

    async def test_a_refusal_of_the_gates_is_raised_not_swallowed(self):
        refusal = BrowserUnavailableError("the sandbox refuses the browser")
        context = context_with({BROWSER_SERVICE: Access(error=refusal)})
        with pytest.raises(BrowserUnavailableError, match="sandbox refuses"):
            await agent_session(context)

    def test_something_else_under_the_key_is_not_an_access_point(self):
        context = context_with({BROWSER_SERVICE: "not a service"})
        assert browser_access(context) is None


async def test_a_tool_without_a_published_browser_says_the_plugin_is_missing():
    result = await BrowserNavigateTool()._execute(
        {"url": "http://shop.test/"}, context=context_with({})
    )
    assert "need the browser plugin" in result.error
