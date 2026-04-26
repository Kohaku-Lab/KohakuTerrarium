"""In-run (mid-turn) auto-compact triggering.

Drives ``AgentHandlersMixin._run_controller_loop`` directly with a
strip-down agent stub. The real per-turn helpers (``_run_single_turn``,
``_emit_token_usage``, ``_collect_and_push_feedback``, …) are replaced
with deterministic fakes so the test can assert exactly when
``CompactManager.trigger_compact`` fires across multiple loop iterations
within a single user request.

The unit guarantees being verified:

1. Compact checks can happen between turns within a single
   ``_process_event`` so summarization can overlap the ongoing run.
2. The helper ignores duplicate attempts while a compact job is already
   running.
3. Below-threshold runs never trigger.
4. The helper is null-safe when ``compact_manager is None`` or when the
   controller never recorded a usage dict.
"""

import pytest

from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
from kohakuterrarium.core.agent_tools import _TurnResult


class _RecordingCompact:
    """Minimal ``CompactManager`` substitute that records calls.

    Mirrors ``should_compact``'s real semantics: tokens-vs-threshold,
    plus a busy flag that ignores duplicate dispatch attempts.
    """

    def __init__(self, threshold_tokens: int = 800):
        self._threshold = threshold_tokens
        self._busy = False
        self.trigger_calls = 0
        self.last_prompt_tokens: int | None = None

    def should_compact(self, prompt_tokens: int) -> bool:
        self.last_prompt_tokens = prompt_tokens
        if self._busy:
            return False
        return prompt_tokens >= self._threshold

    def trigger_compact(self) -> bool:
        if self._busy:
            return False
        self.trigger_calls += 1
        self._busy = True
        return True


class _FakeController:
    """Bare object — no MagicMock. ``_last_usage`` is set by
    ``_emit_token_usage`` per iteration."""

    def __init__(self):
        self._interrupted = False
        self._last_usage: dict = {}


class _LoopAgent(AgentHandlersMixin):
    """Strip-down agent that exposes just the surface
    ``_run_controller_loop`` reaches into.

    Heavy collaborators (output router, real controller, tool dispatch)
    are replaced with deterministic fakes; ``_maybe_trigger_compact`` is
    inherited *unchanged* from the mixin so the test exercises the real
    helper.
    """

    def __init__(self, *, turns_to_run: int, usages: list[dict]):
        # Sanity: usages must cover every turn the loop will run.
        assert len(usages) == turns_to_run
        self._interrupt_requested = False
        self._turns_to_run = turns_to_run
        self._usages = list(usages)
        # Iteration counter — bumped inside ``_collect_and_push_feedback``
        # so it reflects "turns already integrated".
        self._iter = 0
        # Real helper field used by mid-loop + turn-end compact checks
        # exists so we can plug a recording stub here.
        self.compact_manager = _RecordingCompact()
        # Track sequence: index = compact-check ordinal,
        # value = prompt_tokens seen at that check.
        self.compact_check_log: list[int] = []

    # ── Loop dependencies (stubbed) ──────────────────────────────────

    def _reset_output_state(self) -> None:
        pass

    async def _flush_output(self) -> None:
        pass

    def _emit_token_usage(self, controller: _FakeController) -> None:
        controller._last_usage = self._usages[self._iter]

    def _check_termination(self, round_text) -> bool:
        return False

    def _cancel_handles(self, handles) -> None:
        pass

    async def _run_single_turn(self, controller) -> _TurnResult:
        return _TurnResult()

    async def _collect_and_push_feedback(
        self, controller, handles, handle_order, native_tool_call_ids, native_mode
    ) -> bool:
        self._iter += 1
        return self._iter < self._turns_to_run

    # ── Real helper instrumentation ──────────────────────────────────
    #
    # Wrap ``_maybe_trigger_compact`` so we can witness *which*
    # ``prompt_tokens`` value it saw at each call site without changing
    # the production helper.

    def _maybe_trigger_compact(self, controller) -> None:  # type: ignore[override]
        last_usage = getattr(controller, "_last_usage", {}) or {}
        self.compact_check_log.append(last_usage.get("prompt_tokens", 0))
        super()._maybe_trigger_compact(controller)


# ─────────────────────────────────────────────────────────────────────
# Mid-loop trigger behaviour
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact_fires_mid_loop_when_threshold_crossed():
    """Three-turn run; turn 2 crosses threshold. Compact must fire
    *between* turn 2 and turn 3, proving the mid-loop hook works — not
    only at ``_finalize_processing`` after the loop returns."""
    agent = _LoopAgent(
        turns_to_run=3,
        usages=[
            {"prompt_tokens": 100},
            {"prompt_tokens": 900},
            {"prompt_tokens": 950},
        ],
    )
    controller = _FakeController()

    await agent._run_controller_loop(controller, all_round_text=[])

    assert agent.compact_manager.trigger_calls == 1
    assert agent.compact_check_log == [100, 900]


@pytest.mark.asyncio
async def test_compact_does_not_fire_below_threshold():
    agent = _LoopAgent(
        turns_to_run=2,
        usages=[
            {"prompt_tokens": 100},
            {"prompt_tokens": 200},
        ],
    )
    controller = _FakeController()

    await agent._run_controller_loop(controller, all_round_text=[])

    assert agent.compact_manager.trigger_calls == 0
    assert agent.compact_check_log == [100]


@pytest.mark.asyncio
async def test_compact_only_once_per_run_when_already_busy():
    """If the threshold stays crossed across turns, the busy guard must
    suppress redundant dispatch attempts while one compact job is active."""
    agent = _LoopAgent(
        turns_to_run=4,
        usages=[
            {"prompt_tokens": 900},
            {"prompt_tokens": 950},
            {"prompt_tokens": 980},
            {"prompt_tokens": 990},
        ],
    )
    controller = _FakeController()

    await agent._run_controller_loop(controller, all_round_text=[])

    assert agent.compact_manager.trigger_calls == 1
    assert agent.compact_check_log == [900, 950, 980]


# ─────────────────────────────────────────────────────────────────────
# Helper null-safety (mid-loop + turn-end helper)
# ─────────────────────────────────────────────────────────────────────


def test_maybe_trigger_compact_no_manager():
    agent = _LoopAgent(turns_to_run=1, usages=[{"prompt_tokens": 999_999}])
    agent.compact_manager = None
    controller = _FakeController()
    controller._last_usage = {"prompt_tokens": 999_999}

    # Should be a no-op, not raise.
    AgentHandlersMixin._maybe_trigger_compact(agent, controller)


def test_maybe_trigger_compact_missing_last_usage():
    """Controller that never recorded usage (e.g. LLM call failed before
    any tokens streamed) must not crash the helper."""
    agent = _LoopAgent(turns_to_run=1, usages=[{"prompt_tokens": 0}])
    controller = _FakeController()
    # Simulate the attribute being missing entirely.
    del controller._last_usage

    AgentHandlersMixin._maybe_trigger_compact(agent, controller)
    assert agent.compact_manager.trigger_calls == 0


def test_maybe_trigger_compact_none_last_usage():
    """``_last_usage`` set to ``None`` (some providers do this
    explicitly when usage is unavailable) must also be safe."""
    agent = _LoopAgent(turns_to_run=1, usages=[{"prompt_tokens": 0}])
    controller = _FakeController()
    controller._last_usage = None  # type: ignore[assignment]

    AgentHandlersMixin._maybe_trigger_compact(agent, controller)
    assert agent.compact_manager.trigger_calls == 0
