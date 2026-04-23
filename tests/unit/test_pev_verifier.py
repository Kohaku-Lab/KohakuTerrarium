"""Tests for kt-biome PEV (Plan -> Execute -> Verify) plugin.

The plugin lives outside the ``kohakuterrarium`` package, at
``kt-biome/kt_biome/plugins/pev_verifier.py``. These tests add that
sibling repo's root to ``sys.path`` for the duration of the module
so the unit tests can import the plugin without requiring kt-biome to
be pip-installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_KT_BIOME_ROOT = Path(__file__).resolve().parents[2] / "kt-biome"
if str(_KT_BIOME_ROOT) not in sys.path:
    sys.path.insert(0, str(_KT_BIOME_ROOT))

from kt_biome.plugins.pev_verifier import (  # noqa: E402
    PEVVerifierPlugin,
    _last_assistant_message,
    _recent_tool_call_present,
)
from kohakuterrarium.modules.plugin.base import PluginContext  # noqa: E402

# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


class _FakeScratchpad:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def to_dict(self) -> dict[str, str]:
        return dict(self._data)


class _FakeController:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def push_event_sync(self, event: Any) -> None:
        self.events.append(event)


class _FakeAgent:
    """Bare-bones stand-in for kohakuterrarium.core.agent.Agent in tests."""

    def __init__(self) -> None:
        self.scratchpad = _FakeScratchpad()
        self.controller = _FakeController()
        self.session_store = None


def _make_ctx(agent_name: str = "host") -> tuple[PluginContext, _FakeAgent]:
    fake_agent = _FakeAgent()
    ctx = PluginContext(agent_name=agent_name, _agent=fake_agent)
    return ctx, fake_agent


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


class TestPEVVerifierPluginConstruction:
    def test_plugin_disables_without_options(self) -> None:
        """No acceptance_criteria => plugin marks itself disabled, no crash."""
        plugin = PEVVerifierPlugin()
        assert plugin._disabled is True
        assert plugin._criteria == []

    def test_plugin_disables_with_empty_criteria(self) -> None:
        """Empty list treated the same as missing."""
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": []})
        assert plugin._disabled is True

    def test_plugin_registers_with_criteria(self) -> None:
        """Given acceptance_criteria, the plugin initialises fully."""
        opts = {
            "acceptance_criteria": [
                "All files the assistant said it edited exist.",
                "No TODO/FIXME markers were introduced.",
            ],
            "model": "codex/gpt-5.4",
            "trigger_on_keyword": "all done",
            "trigger_on_tool": "done",
            "max_rounds": 2,
            "agent_names": ["swe"],
            "verifier_tools": ["read", "grep"],
        }
        plugin = PEVVerifierPlugin(options=opts)
        assert plugin._disabled is False
        assert len(plugin._criteria) == 2
        assert plugin._model == "codex/gpt-5.4"
        assert plugin._max_rounds == 2
        assert plugin._agent_names == {"swe"}
        assert plugin._verifier_tool_names == ["read", "grep"]
        assert plugin._keyword_re is not None
        assert plugin._keyword_re.search("ALL DONE!") is not None

    def test_plugin_handles_invalid_regex(self) -> None:
        """Invalid regex keyword should degrade gracefully, not raise."""
        plugin = PEVVerifierPlugin(
            options={
                "acceptance_criteria": ["criterion"],
                "trigger_on_keyword": "[unclosed",
            }
        )
        assert plugin._keyword_re is None
        assert plugin._disabled is False

    def test_plugin_accepts_scalar_criteria_string(self) -> None:
        """A single-string acceptance_criteria is wrapped, not rejected."""
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": "single criterion"})
        assert plugin._criteria == ["single criterion"]
        assert plugin._disabled is False


class TestShouldApply:
    def test_disabled_never_applies(self) -> None:
        plugin = PEVVerifierPlugin()
        ctx, _ = _make_ctx("swe")
        assert plugin.should_apply(ctx) is False

    def test_empty_agent_names_applies_to_all(self) -> None:
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": ["c"]})
        ctx, _ = _make_ctx("anything")
        assert plugin.should_apply(ctx) is True

    def test_restricts_to_configured_agents(self) -> None:
        plugin = PEVVerifierPlugin(
            options={"acceptance_criteria": ["c"], "agent_names": ["swe"]}
        )
        assert plugin.should_apply(_make_ctx("swe")[0]) is True
        assert plugin.should_apply(_make_ctx("researcher")[0]) is False


class TestDoneTriggerDetection:
    """Exercise _is_generator_done on representative message shapes."""

    def _plugin(self, **overrides: Any) -> PEVVerifierPlugin:
        opts: dict[str, Any] = {"acceptance_criteria": ["criterion"]}
        opts.update(overrides)
        return PEVVerifierPlugin(options=opts)

    def test_no_assistant_message_is_not_done(self) -> None:
        plugin = self._plugin(trigger_on_keyword="all done")
        assert (
            plugin._is_generator_done([{"role": "user", "content": "hi"}], "all done")
            is False
        )

    def test_tool_calls_on_last_message_defeats_completion(self) -> None:
        plugin = self._plugin(trigger_on_keyword="all done")
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "all done",
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ]
        assert plugin._is_generator_done(messages, "all done") is False

    def test_keyword_regex_triggers_done(self) -> None:
        plugin = self._plugin(trigger_on_keyword="all done")
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok, all done here!"},
        ]
        assert plugin._is_generator_done(messages, "ok, all done here!") is True

    def test_keyword_missing_does_not_trigger_done(self) -> None:
        plugin = self._plugin(trigger_on_keyword="all done")
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "still working"},
        ]
        assert plugin._is_generator_done(messages, "still working") is False

    def test_trigger_on_tool_detects_done_call_in_window(self) -> None:
        """A prior assistant message with the `done` tool call counts."""
        plugin = self._plugin(trigger_on_tool="done")
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "wrapping up",
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "done", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "ok"},
            {"role": "assistant", "content": "All finished."},
        ]
        # Last assistant has no tool_calls; `done` was called earlier
        # in this generator round => trigger fires.
        assert plugin._is_generator_done(messages, "All finished.") is True

    def test_trigger_on_tool_ignores_earlier_rounds(self) -> None:
        """A `done` call before a fresh user turn must NOT trigger."""
        plugin = self._plugin(trigger_on_tool="done")
        messages = [
            {
                "role": "assistant",
                "content": "previous round",
                "tool_calls": [
                    {
                        "id": "t0",
                        "type": "function",
                        "function": {"name": "done", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "new request"},
            {"role": "assistant", "content": "Still working on it"},
        ]
        assert plugin._is_generator_done(messages, "Still working on it") is False


class TestHelpers:
    def test_last_assistant_message_handles_empty(self) -> None:
        assert _last_assistant_message([]) is None
        assert _last_assistant_message([{"role": "user", "content": "hi"}]) is None

    def test_last_assistant_message_returns_latest(self) -> None:
        messages = [
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": "latest"},
        ]
        last = _last_assistant_message(messages)
        assert last is not None and last["content"] == "latest"

    def test_recent_tool_call_present_false_when_absent(self) -> None:
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "just talking"},
        ]
        assert _recent_tool_call_present(messages, "done") is False

    def test_recent_tool_call_present_true_with_nested_function(self) -> None:
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "done", "arguments": "{}"},
                    }
                ],
            },
        ]
        assert _recent_tool_call_present(messages, "done") is True


class TestVerdictCaptureAndFeedback:
    """Cover the post-verdict branches without spinning up a real agent."""

    @pytest.mark.asyncio
    async def test_mark_passed_writes_scratchpad(self) -> None:
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": ["c"]})
        ctx, fake_agent = _make_ctx()
        await plugin.on_load(ctx)

        plugin._mark_passed()

        assert fake_agent.scratchpad.to_dict().get("pev:passed") == "true"

    @pytest.mark.asyncio
    async def test_inject_feedback_pushes_user_event(self) -> None:
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": ["c"]})
        ctx, fake_agent = _make_ctx()
        await plugin.on_load(ctx)

        plugin._inject_feedback(["missing file foo.py", "tests failed"])

        assert len(fake_agent.controller.events) == 1
        event = fake_agent.controller.events[0]
        assert event.type == "user_input"
        text = event.get_text_content()
        assert "evaluator found these problems" in text
        assert "missing file foo.py" in text
        assert "tests failed" in text
        assert event.context.get("source") == "pev_verifier"

    def test_capture_verdict_stores_tuple(self) -> None:
        plugin = PEVVerifierPlugin(options={"acceptance_criteria": ["c"]})
        plugin._capture_verdict(False, ["one", "two"])
        assert plugin._last_verdict == (False, ["one", "two"])
        plugin._capture_verdict(True, [])
        assert plugin._last_verdict == (True, [])
