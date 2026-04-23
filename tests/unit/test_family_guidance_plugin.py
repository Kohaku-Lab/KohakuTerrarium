"""Tests for kt-biome family_guidance plugin."""

from __future__ import annotations

import pytest

from kohakuterrarium.modules.plugin.base import PluginContext
from kt_biome.plugins.family_guidance import (
    GEMINI_FAMILY_GUIDANCE,
    OPENAI_FAMILY_GUIDANCE,
    FamilyGuidancePlugin,
    _contains_sentinel,
    _sentinel,
)


def _make_plugin(**options) -> FamilyGuidancePlugin:
    plugin = FamilyGuidancePlugin(options=options or None)
    return plugin


def _ctx(agent_name: str = "tester", model: str = "") -> PluginContext:
    return PluginContext(agent_name=agent_name, model=model)


@pytest.mark.asyncio
async def test_loads_with_defaults():
    """Plugin loads with default profiles active."""
    plugin = _make_plugin()
    await plugin.on_load(_ctx())
    names = [p.name for p in plugin._profiles]
    assert "openai-family" in names
    assert "gemini-family" in names
    assert plugin.should_apply(_ctx()) is True


@pytest.mark.asyncio
async def test_injects_openai_guidance_for_gpt54():
    """A ``gpt-5.4`` model id triggers the openai-family guidance."""
    plugin = _make_plugin()
    await plugin.on_load(_ctx(model="gpt-5.4"))
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello."},
    ]
    result = await plugin.pre_llm_call(messages, model="gpt-5.4")
    assert result is not None
    assert len(result) == len(messages) + 1
    # Inserted right after the first system message.
    injected = result[1]
    assert injected["role"] == "system"
    assert _sentinel("openai-family") in injected["content"]
    # At least one sentence of the real guidance is present.
    assert "Execution discipline" in injected["content"]
    assert OPENAI_FAMILY_GUIDANCE.splitlines()[0] in injected["content"]
    # Gemini guidance must NOT fire for a gpt model.
    assert _sentinel("gemini-family") not in injected["content"]


@pytest.mark.asyncio
async def test_injects_gemini_guidance_for_gemini31pro():
    """A ``gemini-3.1-pro`` model id triggers the gemini-family guidance."""
    plugin = _make_plugin()
    await plugin.on_load(_ctx(model="gemini-3.1-pro"))
    messages = [{"role": "user", "content": "ping"}]
    result = await plugin.pre_llm_call(messages, model="gemini-3.1-pro")
    assert result is not None
    # No prior system — guidance is inserted at position 0 (no system
    # found, fallback places injection before the first user message).
    injected = result[0]
    assert injected["role"] == "system"
    assert _sentinel("gemini-family") in injected["content"]
    assert "Operational directives" in injected["content"]
    assert GEMINI_FAMILY_GUIDANCE.splitlines()[0] in injected["content"]


@pytest.mark.asyncio
async def test_dedup_prevents_double_inject():
    """Two calls with the same model must not double-inject guidance."""
    plugin = _make_plugin()
    await plugin.on_load(_ctx(model="gpt-5.4"))
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "first"},
    ]
    first = await plugin.pre_llm_call(messages, model="gpt-5.4")
    assert first is not None
    # Sanity: sentinel present.
    assert _contains_sentinel(first, _sentinel("openai-family"))

    # Simulate the framework passing the already-augmented messages back
    # in (which is exactly what happens turn-to-turn).
    second = await plugin.pre_llm_call(first, model="gpt-5.4")
    assert second is None  # Nothing new to inject.

    # And on an unrelated model we still no-op.
    other = await plugin.pre_llm_call(
        [{"role": "user", "content": "x"}], model="claude-opus-4"
    )
    assert other is None


@pytest.mark.asyncio
async def test_agent_names_gating():
    """``agent_names`` option restricts which agents receive guidance."""
    plugin = _make_plugin(agent_names=["only-this-one"])
    await plugin.on_load(_ctx(agent_name="other", model="gpt-5.4"))
    result = await plugin.pre_llm_call(
        [{"role": "user", "content": "hi"}], model="gpt-5.4"
    )
    assert result is None


@pytest.mark.asyncio
async def test_empty_or_weird_messages_are_safe():
    """Invalid inputs never raise; they just fall through."""
    plugin = _make_plugin()
    await plugin.on_load(_ctx(model=""))
    assert await plugin.pre_llm_call([], model="gpt-5.4") is None
    assert await plugin.pre_llm_call("not-a-list", model="gpt-5.4") is None  # type: ignore[arg-type]
    # No model available anywhere → no-op.
    assert (
        await plugin.pre_llm_call([{"role": "user", "content": "x"}], model="") is None
    )


@pytest.mark.asyncio
async def test_custom_profile_appends():
    """User-supplied profile fires alongside defaults."""
    plugin = _make_plugin(
        profiles=[
            {
                "name": "my-family",
                "patterns": [r"^my-provider/.*"],
                "guidance": "Follow the local house rules.",
            }
        ],
    )
    await plugin.on_load(_ctx(model="my-provider/foo-1"))
    result = await plugin.pre_llm_call(
        [{"role": "user", "content": "hi"}], model="my-provider/foo-1"
    )
    assert result is not None
    injected = result[0]
    assert _sentinel("my-family") in injected["content"]
    assert "house rules" in injected["content"]
