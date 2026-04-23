"""Tests for ``BasePlugin.should_apply`` gating (cluster B.4).

Covers declarative ``applies_to`` filters, method-override escape
hatch, AND-semantics when combining both, and the default (no filter)
behavior.
"""

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager


class _AgentNameGated(BasePlugin):
    name = "agent_gated"
    applies_to = {"agent_names": ["swe"]}


class _ModelPatternGated(BasePlugin):
    name = "model_gated"
    applies_to = {"model_patterns": [r"^codex/"]}


class _BothGated(BasePlugin):
    name = "both_gated"
    applies_to = {
        "agent_names": ["swe"],
        "model_patterns": [r"^codex/"],
    }


class _MethodOverrideGated(BasePlugin):
    name = "method_gated"
    applies_to = {"agent_names": ["swe"]}

    def should_apply(self, context):
        # Declarative must pass first, then our dynamic gate.
        return super().should_apply(context) and context.model.startswith("codex/")


class _Unrestricted(BasePlugin):
    name = "unrestricted"


# ── Tests ────────────────────────────────────────────────────────────


def test_agent_name_gate_matches():
    plugin = _AgentNameGated()
    ctx = PluginContext(agent_name="swe")
    assert plugin.should_apply(ctx) is True


def test_agent_name_gate_rejects_non_match():
    plugin = _AgentNameGated()
    ctx = PluginContext(agent_name="reviewer")
    assert plugin.should_apply(ctx) is False


def test_model_pattern_gate_matches():
    plugin = _ModelPatternGated()
    ctx = PluginContext(agent_name="any", model="codex/gpt-5")
    assert plugin.should_apply(ctx) is True


def test_model_pattern_gate_rejects_non_match():
    plugin = _ModelPatternGated()
    ctx = PluginContext(agent_name="any", model="openai/gpt-4")
    assert plugin.should_apply(ctx) is False


def test_both_filters_and_semantics():
    plugin = _BothGated()
    # Both satisfied.
    assert plugin.should_apply(PluginContext(agent_name="swe", model="codex/gpt-5"))
    # Only agent name matches.
    assert not plugin.should_apply(
        PluginContext(agent_name="swe", model="openai/gpt-4")
    )
    # Only model matches.
    assert not plugin.should_apply(
        PluginContext(agent_name="reviewer", model="codex/gpt-5")
    )


def test_method_override_combines_with_declarative():
    plugin = _MethodOverrideGated()
    # Declarative agent_names passes AND dynamic codex-only check passes.
    assert plugin.should_apply(PluginContext(agent_name="swe", model="codex/gpt-5"))
    # Declarative passes, dynamic fails.
    assert not plugin.should_apply(
        PluginContext(agent_name="swe", model="openai/gpt-4")
    )
    # Declarative fails — method override gate is irrelevant.
    assert not plugin.should_apply(
        PluginContext(agent_name="reviewer", model="codex/gpt-5")
    )


def test_missing_applies_to_applies_to_all():
    plugin = _Unrestricted()
    assert plugin.should_apply(PluginContext(agent_name="whatever", model="any/x"))
    assert plugin.should_apply(PluginContext())  # defaults everywhere


def test_manager_skips_non_applicable_on_hooks():
    """Gated plugin skipped entirely when context mismatches."""
    observed: list[str] = []

    class Observer(BasePlugin):
        name = "observer"
        applies_to = {"agent_names": ["other"]}

        async def on_event(self, event=None):
            observed.append("fired")

    manager = PluginManager()
    manager.register(Observer())
    manager._load_context = PluginContext(agent_name="swe")

    import asyncio

    async def run():
        await manager.notify("on_event", event=None)

    asyncio.run(run())
    assert observed == []


def test_manager_includes_applicable_on_hooks():
    observed: list[str] = []

    class Observer(BasePlugin):
        name = "observer"
        applies_to = {"agent_names": ["swe"]}

        async def on_event(self, event=None):
            observed.append("fired")

    manager = PluginManager()
    manager.register(Observer())
    manager._load_context = PluginContext(agent_name="swe")

    import asyncio

    async def run():
        await manager.notify("on_event", event=None)

    asyncio.run(run())
    assert observed == ["fired"]


def test_invalid_regex_logged_but_does_not_crash():
    """Malformed model_patterns regex must not raise at construction."""

    class BadRegex(BasePlugin):
        name = "bad"
        applies_to = {"model_patterns": ["(("]}  # invalid

    plugin = BadRegex()  # construction must succeed
    # With no valid pattern, declarative model filter falls through
    # and the plugin applies to any context.
    assert plugin.should_apply(PluginContext(model="anything"))
