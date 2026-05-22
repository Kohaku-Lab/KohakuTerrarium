"""Tool-catalog gating under ``KT_PROFILE=mobile``.

Pins the contract: the hidden-tool list controls which tools the
mobile build exposes.  Tests use a hand-registered fake-tool +
monkeypatched ``_MOBILE_HIDDEN_TOOLS`` so they don't depend on the
real catalog's content (which can grow / shrink over time).
"""

import pytest

from kohakuterrarium.builtins import tool_catalog


class _FakeTool:
    """Minimal stand-in for ``BaseTool`` — instantiable, returns
    the config it was given, doesn't touch anything else."""

    def __init__(self, config=None):
        self.config = config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KT_PROFILE", raising=False)


@pytest.fixture
def with_fake_tools(monkeypatch):
    """Register a couple of fake tools at module scope, then clean
    up so other tests don't see them."""
    monkeypatch.setitem(tool_catalog._BUILTIN_TOOLS, "_test_visible", _FakeTool)
    monkeypatch.setitem(tool_catalog._BUILTIN_TOOLS, "_test_hidden", _FakeTool)
    yield
    # monkeypatch.setitem cleans up automatically.


class TestNoMobileProfile:
    def test_both_tools_visible(self, with_fake_tools):
        # Default profile (off): both tools resolve.
        assert tool_catalog.is_builtin_tool("_test_visible") is True
        assert tool_catalog.is_builtin_tool("_test_hidden") is True
        assert tool_catalog.get_builtin_tool("_test_visible") is not None
        assert tool_catalog.get_builtin_tool("_test_hidden") is not None
        names = tool_catalog.list_builtin_tools()
        assert "_test_visible" in names
        assert "_test_hidden" in names


class TestMobileProfileHidesListed:
    def test_hidden_tool_unreachable(self, with_fake_tools, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setattr(
            tool_catalog, "_MOBILE_HIDDEN_TOOLS", frozenset({"_test_hidden"})
        )
        # Lookup contract: hidden tool reports as not-a-builtin and
        # get_builtin_tool returns None (same shape as a missing
        # tool — so the LLM can't accidentally invoke it by name).
        assert tool_catalog.is_builtin_tool("_test_hidden") is False
        assert tool_catalog.get_builtin_tool("_test_hidden") is None

    def test_visible_tool_unaffected(self, with_fake_tools, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setattr(
            tool_catalog, "_MOBILE_HIDDEN_TOOLS", frozenset({"_test_hidden"})
        )
        # The OTHER tool stays visible — the filter is opt-out per
        # name, not a blanket disable.
        assert tool_catalog.is_builtin_tool("_test_visible") is True
        assert tool_catalog.get_builtin_tool("_test_visible") is not None

    def test_list_omits_hidden(self, with_fake_tools, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setattr(
            tool_catalog, "_MOBILE_HIDDEN_TOOLS", frozenset({"_test_hidden"})
        )
        names = tool_catalog.list_builtin_tools()
        assert "_test_visible" in names
        assert "_test_hidden" not in names


class TestEmptyHiddenSet:
    def test_no_filter_when_set_is_empty(self, with_fake_tools, monkeypatch):
        # Default repo state ships with an empty ``_MOBILE_HIDDEN_TOOLS``
        # (bundled sandbox makes shell tools work).  Pin that the empty
        # set means "no filtering" — so a future contributor doesn't
        # accidentally regress by introducing a "list" check that
        # treats empty as "hide everything".
        monkeypatch.setenv("KT_PROFILE", "mobile")
        # _MOBILE_HIDDEN_TOOLS at its real (empty) default.
        names = tool_catalog.list_builtin_tools()
        assert "_test_visible" in names
        assert "_test_hidden" in names
        assert tool_catalog.get_builtin_tool("_test_hidden") is not None
