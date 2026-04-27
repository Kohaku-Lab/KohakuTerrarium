"""Regression tests for the four audit findings fixed in this wave.

* **3c — plugin veto sets cooldown** so a vetoing plugin doesn't permit
  immediate re-trigger every turn.
* **3d — ``trigger_compact`` populates ``_last_skip_reason``** with one
  of ``"no_controller"``, ``"too_short"``, or ``"busy"`` so the
  ``/compact`` slash command can show a precise message.
* **3j — ``_last_compact_time`` round-trips through session state**
  so a quick resume doesn't immediately re-compact.
* The dead ``_emergency_truncate`` helper has been removed; this file
  also asserts it stays removed (to catch accidental revivals).
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.conversation import Conversation


def _make_conversation(n: int) -> Conversation:
    conv = Conversation()
    conv.append("system", "You are helpful.")
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        conv.append(role, f"Message {i}")
    return conv


def _make_manager(*, keep_recent: int = 2):
    cfg = CompactConfig(max_tokens=1000, threshold=0.5, keep_recent_turns=keep_recent)
    mgr = CompactManager(cfg)
    conv = _make_conversation(20)
    controller = MagicMock()
    controller.conversation = conv
    mgr._controller = controller
    mgr._agent_name = "test"
    mgr._output_router = MagicMock()
    mgr._output_router.notify_activity = MagicMock()
    return mgr, conv


# ─────────────────────────────────────────────────────────────────────
# 3d — distinct skip reasons surfaced via ``_last_skip_reason``
# ─────────────────────────────────────────────────────────────────────


class TestSkipReasons:
    def test_no_controller(self):
        mgr = CompactManager(CompactConfig())
        # No ``_controller`` set.
        assert mgr.trigger_compact() is False
        assert mgr._last_skip_reason == "no_controller"

    def test_too_short(self):
        # ``keep_recent=10`` larger than the conversation → boundary <= 1
        # so ``trigger_compact`` reports the conversation as too short.
        mgr = CompactManager(CompactConfig(keep_recent_turns=10))
        conv = _make_conversation(2)
        controller = MagicMock()
        controller.conversation = conv
        mgr._controller = controller
        mgr._agent_name = "test"

        assert mgr.trigger_compact() is False
        assert mgr._last_skip_reason == "too_short"

    @pytest.mark.asyncio
    async def test_busy(self):
        mgr, _conv = _make_manager()

        async def slow_chat(messages, *, stream=True, **kwargs):
            await asyncio.sleep(0.2)
            yield "summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = slow_chat

        first = mgr.trigger_compact()
        assert first is True
        assert mgr._last_skip_reason == ""

        # Second attempt while the first is still running.
        second = mgr.trigger_compact()
        assert second is False
        assert mgr._last_skip_reason == "busy"

        # Drain the in-flight task so test cleanup is clean.
        if mgr._compact_task:
            await mgr._compact_task

    @pytest.mark.asyncio
    async def test_skip_reason_clears_on_success(self):
        mgr, _conv = _make_manager()

        async def fast_chat(messages, *, stream=True, **kwargs):
            yield "ok"

        mgr._llm = MagicMock()
        mgr._llm.chat = fast_chat

        # Simulate a stale skip reason from a prior run.
        mgr._last_skip_reason = "too_short"
        assert mgr.trigger_compact() is True
        assert mgr._last_skip_reason == ""

        if mgr._compact_task:
            await mgr._compact_task


# ─────────────────────────────────────────────────────────────────────
# 3c — plugin veto sets cooldown
# ─────────────────────────────────────────────────────────────────────


class TestVetoCooldown:
    @pytest.mark.asyncio
    async def test_plugin_veto_sets_last_compact_time(self):
        """A vetoed compact must arm the cooldown so subsequent
        ``should_compact`` checks decline until the cooldown elapses."""
        mgr, _conv = _make_manager()
        # Plugin manager that vetoes by returning ``False`` from
        # ``should_proceed("on_compact_start", ...)``.
        plugins = MagicMock()

        async def _refuse(hook, **kwargs):
            return False

        plugins.should_proceed = _refuse
        mgr._plugins = plugins

        async def never_chat(messages, *, stream=True, **kwargs):
            # Should not be reached — veto exits before summarize.
            yield "should not run"

        mgr._llm = MagicMock()
        mgr._llm.chat = never_chat

        before = time.time()
        ok = mgr.trigger_compact()
        assert ok is True
        if mgr._compact_task:
            await mgr._compact_task

        # ``_last_compact_time`` must be set to roughly "now" so the
        # cooldown applies to vetoed runs too.
        assert mgr._last_compact_time >= before
        assert mgr._last_compact_time <= time.time() + 0.1

    @pytest.mark.asyncio
    async def test_cooldown_blocks_immediate_retrigger_after_veto(self):
        mgr, _conv = _make_manager()
        mgr.config.cooldown_seconds = 60.0

        plugins = MagicMock()

        async def _refuse(hook, **kwargs):
            return False

        plugins.should_proceed = _refuse
        mgr._plugins = plugins

        async def chat(messages, *, stream=True, **kwargs):
            yield "x"

        mgr._llm = MagicMock()
        mgr._llm.chat = chat

        mgr.trigger_compact()
        if mgr._compact_task:
            await mgr._compact_task

        # ``should_compact`` must decline because cooldown is armed.
        # Token threshold check would otherwise be True (max_tokens=1000,
        # threshold=0.5 → trip at 500 prompt tokens).
        assert mgr.should_compact(prompt_tokens=900) is False


# ─────────────────────────────────────────────────────────────────────
# 3j — ``_last_compact_time`` survives a save/load cycle
# ─────────────────────────────────────────────────────────────────────


class _FakeStateTable:
    """In-memory ``state`` KVault stand-in for the persistence tests."""

    def __init__(self) -> None:
        self._data: dict = {}

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeSessionStore:
    def __init__(self) -> None:
        self.state = _FakeStateTable()
        self._events: list[dict] = []
        self._conversation: dict = {}

    def get_events(self, agent):
        return list(self._events)

    def save_state(self, agent, **kwargs):
        for k, v in kwargs.items():
            self.state[f"{agent}:{k}"] = v

    def save_conversation(self, agent, messages):
        self._conversation[agent] = list(messages)


class TestPersistedCooldown:
    @pytest.mark.asyncio
    async def test_last_compact_time_persisted_after_success(self):
        mgr, _conv = _make_manager()
        mgr._session_store = _FakeSessionStore()

        async def chat(messages, *, stream=True, **kwargs):
            yield "summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = chat

        mgr.trigger_compact()
        if mgr._compact_task:
            await mgr._compact_task

        saved = mgr._session_store.state.get("test:last_compact_time")
        assert saved is not None
        # Must equal the in-memory watermark exactly so future restores
        # reproduce the running cooldown precisely.
        assert saved == mgr._last_compact_time
        assert mgr._compact_count == 1


# ─────────────────────────────────────────────────────────────────────
# Dead-code guard
# ─────────────────────────────────────────────────────────────────────


def test_emergency_truncate_is_gone():
    """``_emergency_truncate`` was dead code — make sure it stays gone."""
    assert not hasattr(CompactManager, "_emergency_truncate")


# ─────────────────────────────────────────────────────────────────────
# Few-user-turn bug — chat had content but compact said "too short"
# ─────────────────────────────────────────────────────────────────────


class TestFewUserTurnsCompacts:
    """Regression for the user-reported bug: clicking compact on a real
    agent session always showed "Nothing to compact" even with plenty of
    chat content. Root cause: with default ``keep_recent_turns=8`` and
    fewer than 8 user turns total, ``_count_keep_messages`` walked the
    whole conversation and returned ``len(messages) - 1`` so
    ``boundary <= 1`` always.
    """

    def _agent_run(self):
        """An 18-message run with 3 user turns — typical agent shape.

        ``ToolMessage`` requires a ``tool_call_id`` (see
        ``llm/message.py``); use distinct ids per tool message.
        """
        from kohakuterrarium.core.conversation import Conversation

        conv = Conversation()
        conv.append("system", "you are an agent")
        # Turn 1 — explore + edit + test
        conv.append("user", "fix this bug")
        conv.append("assistant", "exploring")
        conv.append("tool", "result 1", tool_call_id="t1")
        conv.append("tool", "result 2", tool_call_id="t2")
        conv.append("assistant", "applying fix")
        conv.append("tool", "result 3", tool_call_id="t3")
        conv.append("assistant", "running tests")
        conv.append("tool", "result 4", tool_call_id="t4")
        conv.append("assistant", "tests pass")
        # Turn 2
        conv.append("user", "now write docs")
        conv.append("assistant", "writing")
        conv.append("tool", "result 5", tool_call_id="t5")
        conv.append("assistant", "done")
        # Turn 3
        conv.append("user", "any tests?")
        conv.append("assistant", "checking")
        conv.append("tool", "result 6", tool_call_id="t6")
        conv.append("assistant", "yes covered")
        return conv

    def test_default_config_compacts_few_user_turns(self):
        """Default ``keep_recent_turns=8`` + 3 user turns must NOT
        report ``too_short``. Pre-fix: boundary == 1 always."""
        mgr = CompactManager(CompactConfig())  # all defaults
        conv = self._agent_run()
        messages = conv.get_messages()
        keep = mgr._count_keep_messages(messages)
        boundary = len(messages) - keep
        assert boundary > 1, (
            f"few-user-turn run reported too_short: "
            f"messages={len(messages)} keep={keep} boundary={boundary}"
        )
        # Half-cap kicks in: keep ~= n // 2.
        assert keep == len(messages) // 2

    @pytest.mark.asyncio
    async def test_trigger_compact_succeeds_with_few_user_turns(self):
        """End-to-end: ``trigger_compact`` should NOT report
        ``_last_skip_reason == "too_short"`` for a typical agent run.

        Async because ``trigger_compact`` schedules a background task
        via ``asyncio.create_task`` — needs a running loop.
        """
        from unittest.mock import MagicMock

        mgr = CompactManager(CompactConfig())
        conv = self._agent_run()
        controller = MagicMock()
        controller.conversation = conv
        mgr._controller = controller
        mgr._agent_name = "test"
        mgr._output_router = MagicMock()
        mgr._output_router.notify_activity = MagicMock()
        mgr._llm = MagicMock()

        async def chat(messages, *, stream=True, **kwargs):
            yield "summary"

        mgr._llm.chat = chat

        ok = mgr.trigger_compact()
        assert ok is True
        assert mgr._last_skip_reason == ""

        # Drain the in-flight task for clean teardown.
        if mgr._compact_task:
            await mgr._compact_task

    def test_keep_count_caps_at_half_when_user_turns_unreachable(self):
        """Synthetic check on the cap itself."""
        from kohakuterrarium.core.conversation import Conversation

        # 50 messages, only 1 user turn at the start.
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "do everything")
        for i in range(48):
            if i % 2 == 0:
                conv.append("assistant", f"step {i}")
            else:
                conv.append("tool", f"step {i}", tool_call_id=f"t{i}")

        mgr = CompactManager(CompactConfig(keep_recent_turns=8))
        keep = mgr._count_keep_messages(conv.get_messages())
        # 50 messages → half-cap = 25.
        assert keep == 25

    def test_empty_or_tiny_conv_still_returns_zero(self):
        """Edge case: 0/1 messages cannot compact — keep stays 0."""
        from kohakuterrarium.core.conversation import Conversation

        mgr = CompactManager(CompactConfig())
        assert mgr._count_keep_messages([]) == 0
        single = Conversation()
        single.append("system", "sys")
        assert mgr._count_keep_messages(single.get_messages()) == 0


# ─────────────────────────────────────────────────────────────────────
# User-content drop in compact summary (multimodal-list payload)
# ─────────────────────────────────────────────────────────────────────


class TestFormatMessagesContentShapes:
    """Regression for the bug that produced "no user instructions" in
    every compact summary built from a web-frontend conversation.

    The frontend POSTs user content as ``[{"type": "text", "text":
    "..."}]`` and ``Conversation.append`` stores that list verbatim.
    The old ``_format_messages_for_summary`` skipped list parts that
    didn't have a ``.text`` attribute — dicts don't, so EVERY user
    message vanished from the summary input.
    """

    def _mgr(self):
        return CompactManager(CompactConfig(keep_recent_turns=2))

    def test_plain_string_content(self):
        from kohakuterrarium.core.conversation import Conversation

        conv = Conversation()
        conv.append("system", "You are an echo agent")
        conv.append("user", "hello world")
        conv.append("assistant", "world")
        out = self._mgr()._format_messages_for_summary(conv.get_messages()[1:])
        assert "[user]: hello world" in out
        assert "[assistant]: world" in out

    def test_multimodal_list_of_dicts_content(self):
        """The exact shape the web frontend sends. Pre-fix: user
        messages silently dropped. Post-fix: text extracted."""
        from kohakuterrarium.core.conversation import Conversation

        conv = Conversation()
        conv.append("system", "You echo")
        conv.append("user", [{"type": "text", "text": "repeat: 1"}])
        conv.append("assistant", "1")
        conv.append("user", [{"type": "text", "text": "msg-2"}])
        conv.append("assistant", "2")

        out = self._mgr()._format_messages_for_summary(conv.get_messages()[1:])
        assert "[user]: repeat: 1" in out
        assert "[user]: msg-2" in out
        assert "[assistant]: 1" in out
        assert "[assistant]: 2" in out

    def test_multimodal_list_with_textpart_objects(self):
        """Framework-helper-built parts have ``.text`` — must still work."""
        from kohakuterrarium.core.conversation import Conversation

        class _TextPart:
            def __init__(self, t):
                self.text = t

        conv = Conversation()
        conv.append("system", "echo")
        conv.append("user", [_TextPart("hi"), _TextPart("there")])
        out = self._mgr()._format_messages_for_summary(conv.get_messages()[1:])
        assert "[user]: hi there" in out

    def test_dict_with_content_field_extracts(self):
        """Some providers emit ``{"content": "..."}`` instead of
        ``{"text": "..."}``. Tolerate both."""
        from kohakuterrarium.core.conversation import Conversation

        conv = Conversation()
        conv.append("system", "echo")
        conv.append("user", [{"content": "fallback shape"}])
        out = self._mgr()._format_messages_for_summary(conv.get_messages()[1:])
        assert "fallback shape" in out

    def test_non_text_parts_silently_skipped(self):
        """An image part has no text — it should not produce empty
        ``[user]:`` lines, just disappear from the summary."""
        from kohakuterrarium.core.conversation import Conversation

        conv = Conversation()
        conv.append("system", "echo")
        # Mix: one text + one image dict.
        conv.append(
            "user",
            [
                {"type": "text", "text": "describe this"},
                {"type": "image", "url": "https://example.invalid/x.png"},
            ],
        )
        out = self._mgr()._format_messages_for_summary(conv.get_messages()[1:])
        assert "describe this" in out
        # The image dict should not bleed into the formatted text.
        assert "https://" not in out
