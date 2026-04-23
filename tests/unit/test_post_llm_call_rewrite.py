"""Tests for ``post_llm_call`` chain-with-return rewrites (cluster B.3).

Covers single-plugin rewrite, chain composition, None pass-through,
and the ``assistant_message_edited`` activity event.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.controller_plugins import run_post_llm_call_chain
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content

    def get_text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return str(self.content)


class _FakeConversation:
    def __init__(self, last: _FakeMessage | None):
        self._last = last

    def get_last_assistant_message(self):
        return self._last


class _FakeRouter:
    def __init__(self):
        self.activity: list[tuple[str, str, dict | None]] = []

    def notify_activity(self, activity_type, detail, metadata=None):
        self.activity.append((activity_type, detail, metadata))


def _make_controller(plugins: list[BasePlugin], last_text: str) -> SimpleNamespace:
    manager = PluginManager()
    for p in plugins:
        manager.register(p)
    manager._load_context = PluginContext(agent_name="a", model="m")
    last = _FakeMessage(last_text)
    router = _FakeRouter()
    return SimpleNamespace(
        plugins=manager,
        conversation=_FakeConversation(last),
        _last_usage={"prompt_tokens": 1, "completion_tokens": 1},
        llm=SimpleNamespace(model="m"),
        output_router=router,
        _last_message=last,
    )


class _AppendMarkPlugin(BasePlugin):
    name = "append_mark"

    def __init__(self, mark: str, priority: int = 50):
        super().__init__()
        self._mark = mark
        self.priority = priority

    async def post_llm_call(self, messages, response, usage, **kwargs):
        return response + self._mark


class _NoopPlugin(BasePlugin):
    name = "noop"


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_plugin_rewrites_response():
    ctrl = _make_controller([_AppendMarkPlugin(" [EDIT]")], "hello")
    await run_post_llm_call_chain(ctrl, [])
    assert ctrl._last_message.content == "hello [EDIT]"
    assert len(ctrl.output_router.activity) == 1
    activity_type, detail, meta = ctrl.output_router.activity[0]
    assert activity_type == "assistant_message_edited"
    assert "append_mark" in detail
    assert meta is not None
    assert meta["edited_by"] == ["append_mark"]
    assert meta["original_preview"] == "hello"


@pytest.mark.asyncio
async def test_chain_composes_three_plugins():
    """Each plugin sees the previous plugin's rewrite."""
    p1 = _AppendMarkPlugin(" [P1]", priority=10)
    p2 = _AppendMarkPlugin(" [P2]", priority=20)
    p3 = _AppendMarkPlugin(" [P3]", priority=30)
    # Give each a distinct name so the marker lists them all.
    p1.name = "p1"
    p2.name = "p2"
    p3.name = "p3"
    ctrl = _make_controller([p1, p2, p3], "body")
    await run_post_llm_call_chain(ctrl, [])
    assert ctrl._last_message.content == "body [P1] [P2] [P3]"
    assert len(ctrl.output_router.activity) == 1
    meta = ctrl.output_router.activity[0][2]
    assert meta["edited_by"] == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_none_passes_through_no_marker():
    ctrl = _make_controller([_NoopPlugin()], "unchanged")
    await run_post_llm_call_chain(ctrl, [])
    assert ctrl._last_message.content == "unchanged"
    assert ctrl.output_router.activity == []


@pytest.mark.asyncio
async def test_empty_string_return_still_counts_as_pass_through():
    """Non-string returns (or same-value returns) don't count as edits."""

    class SameReturn(BasePlugin):
        name = "same"

        async def post_llm_call(self, messages, response, usage, **kwargs):
            return response  # identical — shouldn't mark edited

    ctrl = _make_controller([SameReturn()], "hi")
    await run_post_llm_call_chain(ctrl, [])
    assert ctrl._last_message.content == "hi"
    assert ctrl.output_router.activity == []


@pytest.mark.asyncio
async def test_mixed_active_and_noop_only_lists_editors():
    active = _AppendMarkPlugin(" [E]")
    active.name = "editor"
    active.priority = 20
    noop = _NoopPlugin()
    noop.priority = 10
    ctrl = _make_controller([noop, active], "seed")
    await run_post_llm_call_chain(ctrl, [])
    assert ctrl._last_message.content == "seed [E]"
    meta = ctrl.output_router.activity[0][2]
    assert meta["edited_by"] == ["editor"]
