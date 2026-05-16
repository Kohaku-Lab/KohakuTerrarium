"""Unit tests for small lifecycle / compat helper modules:
:mod:`agent_lifecycle`, :mod:`agent_budget_recovery`, :mod:`agent_compact`.
"""

import asyncio
import types
from unittest.mock import AsyncMock


from kohakuterrarium.core.agent_budget_recovery import (
    _message_to_conversation_json,
    _metadata_for_messages,
    sync_emergency_drop_conversation,
)
from kohakuterrarium.core.agent_compact import (
    AgentCompactMixin,
    restore_compact_state_from_session,
)
from kohakuterrarium.core.agent_lifecycle import AgentLifecycleMixin
from kohakuterrarium.core.conversation import Conversation

# ── agent_lifecycle.AgentLifecycleMixin ──────────────────────────


class _Stopper:
    def __init__(self, fail=False):
        self.stopped = 0
        self.cancelled = 0
        self.fail = fail

    async def stop(self):
        self.stopped += 1

    async def cancel(self):
        self.cancelled += 1

    async def cancel_all(self):
        self.cancelled += 1

    async def stop_all(self):
        self.stopped += 1


def _build_lifecycle_agent(*, has_mcp=False, has_compact=True, compact_llm=None):
    """Build a duck-typed agent that satisfies the lifecycle mixin's surface."""

    class _Agent(AgentLifecycleMixin):
        pass

    a = _Agent()
    a.plugins = AsyncMock()
    a.plugins.notify = AsyncMock()
    a.plugins.unload_all = AsyncMock()
    a.compact_manager = (
        types.SimpleNamespace(
            cancel=AsyncMock(),
            _llm=compact_llm,
        )
        if has_compact
        else None
    )
    a.llm = AsyncMock()
    a.llm.close = AsyncMock()
    a.output_router = types.SimpleNamespace(stop=AsyncMock())
    a.subagent_manager = types.SimpleNamespace(cancel_all=AsyncMock())
    a.trigger_manager = types.SimpleNamespace(stop_all=AsyncMock())
    a.input = types.SimpleNamespace(stop=AsyncMock())
    a.config = types.SimpleNamespace(name="alice")
    a._running = True
    a._shutdown_event = asyncio.Event()
    a.executor = types.SimpleNamespace(_tasks={})
    if has_mcp:
        a._mcp_manager = types.SimpleNamespace(shutdown=AsyncMock())
    return a


class TestAgentLifecycle:
    async def test_full_stop_cascade(self):
        a = _build_lifecycle_agent()
        await a.stop()
        assert a._running is False
        assert a._shutdown_event.is_set()
        # Every dependency stopped.
        a.plugins.notify.assert_awaited_once_with("on_agent_stop")
        a.plugins.unload_all.assert_awaited_once()
        a.subagent_manager.cancel_all.assert_awaited()
        a.trigger_manager.stop_all.assert_awaited()
        a.input.stop.assert_awaited()
        a.compact_manager.cancel.assert_awaited()
        a.output_router.stop.assert_awaited()
        a.llm.close.assert_awaited()

    async def test_mcp_manager_shut_down_when_present(self):
        a = _build_lifecycle_agent(has_mcp=True)
        await a.stop()
        a._mcp_manager.shutdown.assert_awaited()

    async def test_compact_llm_closed_when_distinct(self):
        compact_llm = AsyncMock()
        compact_llm.close = AsyncMock()
        a = _build_lifecycle_agent(compact_llm=compact_llm)
        await a.stop()
        compact_llm.close.assert_awaited()

    async def test_compact_llm_same_as_main_not_double_closed(self):
        a = _build_lifecycle_agent()
        # Same instance as main → not closed twice.
        a.compact_manager._llm = a.llm
        await a.stop()
        # llm.close was awaited exactly once (from the final llm.close call).
        assert a.llm.close.await_count == 1

    async def test_no_plugins_safe(self):
        a = _build_lifecycle_agent()
        a.plugins = None
        # Should not raise.
        await a.stop()

    async def test_no_compact_manager_safe(self):
        a = _build_lifecycle_agent(has_compact=False)
        await a.stop()
        a.llm.close.assert_awaited()

    async def test_cancel_executor_tasks_no_executor(self):
        a = _build_lifecycle_agent()
        a.executor = None
        # Direct call — must not raise.
        await a._cancel_executor_tasks()

    async def test_cancel_executor_tasks_no_tasks(self):
        a = _build_lifecycle_agent()
        # Empty dict — nothing to cancel.
        await a._cancel_executor_tasks()

    async def test_cancel_executor_tasks_with_running(self):
        a = _build_lifecycle_agent()

        async def sleeper():
            await asyncio.sleep(5)

        t = asyncio.create_task(sleeper())
        a.executor._tasks["x"] = t
        await a._cancel_executor_tasks()
        assert t.cancelled() or t.done()


# ── agent_budget_recovery ────────────────────────────────────────


class TestSyncEmergencyDropConversation:
    def test_no_controller_no_op(self):
        agent = types.SimpleNamespace()  # no controller
        sync_emergency_drop_conversation(agent, [])  # must not raise

    def test_replaces_conversation(self):
        original = Conversation()
        original.append("user", "first")
        agent = types.SimpleNamespace(
            controller=types.SimpleNamespace(conversation=original)
        )
        new_messages = [
            {"role": "user", "content": "fresh"},
            {"role": "assistant", "content": "ok"},
        ]
        sync_emergency_drop_conversation(agent, new_messages)
        # Same controller, but conversation object replaced.
        assert agent.controller.conversation is not original
        msgs = agent.controller.conversation.get_messages()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "fresh"

    def test_failure_swallowed(self):
        # A controller whose conversation can't supply metadata triggers
        # an exception that the helper swallows.
        agent = types.SimpleNamespace(
            controller=types.SimpleNamespace(conversation=None)
        )
        # The helper crashes trying to read conversation._metadata; should not raise.
        sync_emergency_drop_conversation(agent, [{"role": "user", "content": "x"}])


class TestMessageToConversationJson:
    def test_known_fields_preserved(self):
        m = {
            "role": "assistant",
            "content": "hi",
            "name": "n",
            "tool_call_id": None,
            "tool_calls": [{"id": "1"}],
        }
        out = _message_to_conversation_json(m)
        assert out["role"] == "assistant"
        assert out["content"] == "hi"
        assert out["tool_calls"] == [{"id": "1"}]
        assert out["extra_fields"] == {}

    def test_extra_fields_captured(self):
        m = {"role": "assistant", "content": "x", "reasoning_content": "secret"}
        out = _message_to_conversation_json(m)
        assert out["extra_fields"] == {"reasoning_content": "secret"}


class TestMetadataForMessages:
    def test_total_chars(self):
        conv = Conversation()
        conv.append("user", "")
        agent = types.SimpleNamespace(
            controller=types.SimpleNamespace(conversation=conv)
        )
        msgs = [{"content": "abc"}, {"content": "de"}]
        meta = _metadata_for_messages(agent, msgs)
        assert meta["message_count"] == 2
        assert meta["total_chars"] == 5


# ── agent_compact ────────────────────────────────────────────────


class TestRestoreCompactState:
    def test_no_state_no_op(self):
        mgr = types.SimpleNamespace()
        store = types.SimpleNamespace(state=None)
        restore_compact_state_from_session(mgr, store, "alice")
        assert not hasattr(mgr, "_compact_count")

    def test_loads_compact_count(self):
        mgr = types.SimpleNamespace()
        store = types.SimpleNamespace(state={"alice:compact_count": "5"})
        restore_compact_state_from_session(mgr, store, "alice")
        assert mgr._compact_count == 5

    def test_loads_last_compact_time(self):
        mgr = types.SimpleNamespace()
        store = types.SimpleNamespace(
            state={
                "alice:compact_count": 2,
                "alice:last_compact_time": "1234.5",
            }
        )
        restore_compact_state_from_session(mgr, store, "alice")
        assert mgr._last_compact_time == 1234.5

    def test_bad_value_silently_ignored(self):
        mgr = types.SimpleNamespace()
        store = types.SimpleNamespace(state={"alice:compact_count": "not-a-num"})
        restore_compact_state_from_session(mgr, store, "alice")
        assert not hasattr(mgr, "_compact_count")


class TestBuildCompactLLM:
    def test_falls_back_to_main_when_profile_resolution_fails(self, monkeypatch):
        # When no profile name is resolvable, falls back to ``self.llm``.
        from kohakuterrarium.core import agent_compact as ac

        monkeypatch.setattr(ac, "resolve_controller_llm", lambda *a, **k: None)

        class _Agent(AgentCompactMixin):
            pass

        a = _Agent()
        a.llm = object()  # sentinel
        a.config = types.SimpleNamespace(
            llm_profile="",
            model="",
            provider="",
            variation_selections={},
            name="alice",
        )
        a._llm_override = None
        from kohakuterrarium.core.compact import CompactConfig

        out = a._build_compact_llm(CompactConfig())
        assert out is a.llm

    def test_uses_explicit_compact_model(self, monkeypatch):
        from kohakuterrarium.core import agent_compact as ac

        seen = []

        def fake_create(name):
            seen.append(name)
            return "compact-llm-instance"

        monkeypatch.setattr(ac, "create_llm_from_profile_name", fake_create)

        class _Agent(AgentCompactMixin):
            pass

        a = _Agent()
        a.llm = object()
        a.config = types.SimpleNamespace(
            llm_profile="",
            model="",
            provider="",
            variation_selections={},
            name="alice",
        )
        a._llm_override = None
        from kohakuterrarium.core.compact import CompactConfig

        out = a._build_compact_llm(CompactConfig(compact_model="my-profile"))
        assert out == "compact-llm-instance"
        assert seen == ["my-profile"]

    def test_create_failure_falls_back(self, monkeypatch):
        from kohakuterrarium.core import agent_compact as ac

        def boom(name):
            raise RuntimeError("create failed")

        monkeypatch.setattr(ac, "create_llm_from_profile_name", boom)

        class _Agent(AgentCompactMixin):
            pass

        a = _Agent()
        a.llm = object()
        a.config = types.SimpleNamespace(
            llm_profile="",
            model="",
            provider="",
            variation_selections={},
            name="alice",
        )
        a._llm_override = None
        from kohakuterrarium.core.compact import CompactConfig

        out = a._build_compact_llm(CompactConfig(compact_model="bad-profile"))
        assert out is a.llm

    def test_resolves_via_controller_data(self, monkeypatch):
        """When no explicit compact_model / override / profile, falls back to
        resolve_controller_llm and uses its result (covers line 46)."""
        from kohakuterrarium.core import agent_compact as ac

        called = []

        def fake_resolve(data, **kw):
            called.append(data)
            return object()  # Truthy "profile"

        def fake_id(p):
            return "resolved/profile"

        def fake_create(name):
            return "compact-llm"

        monkeypatch.setattr(ac, "resolve_controller_llm", fake_resolve)
        monkeypatch.setattr(ac, "profile_to_identifier", fake_id)
        monkeypatch.setattr(ac, "create_llm_from_profile_name", fake_create)

        class _Agent(AgentCompactMixin):
            pass

        a = _Agent()
        a.llm = object()
        # All identifiers empty → falls through to resolve_controller_llm.
        a.config = types.SimpleNamespace(
            llm_profile="",
            model="m",
            provider="p",
            variation_selections={"k": "v"},
            name="alice",
        )
        a._llm_override = None
        from kohakuterrarium.core.compact import CompactConfig

        out = a._build_compact_llm(CompactConfig())
        # resolve_controller_llm was invoked.
        assert called
        # profile_to_identifier was invoked, returning "resolved/profile",
        # then create_llm_from_profile_name was called with that name.
        assert out == "compact-llm"
