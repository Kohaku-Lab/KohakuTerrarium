"""Tests for kt-biome's CircuitBreakerPlugin.

Uses a controllable clock (``plugin._now`` monkey-patched to read a
mutable box) so every timing behaviour is deterministic without real
``asyncio.sleep``.
"""

from __future__ import annotations

import pytest

from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.tool.base import ToolResult
from kt_biome.plugins.circuit_breaker import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    CircuitBreakerPlugin,
)

# ── Helpers ──


class Clock:
    """Mutable monotonic-clock stub."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_plugin(options: dict | None = None) -> tuple[CircuitBreakerPlugin, Clock]:
    plugin = CircuitBreakerPlugin(options)
    clock = Clock()
    # Replace the method with the callable so _now() returns clock().
    plugin._now = clock  # type: ignore[assignment]
    return plugin, clock


def _fail() -> ToolResult:
    return ToolResult(error="boom")


def _ok() -> ToolResult:
    return ToolResult(output="ok", exit_code=0)


# ── Tests ──


def test_defaults_load_cleanly() -> None:
    """1. Loads with defaults and exposes sensible initial state."""
    plugin = CircuitBreakerPlugin()
    assert plugin.name == "circuit_breaker"
    assert plugin._enabled is True
    assert plugin._half_open_trial is True
    assert plugin._default.max_failures == 5
    assert plugin._default.window_seconds == 60
    assert plugin._default.cooldown_seconds == 30
    assert plugin._default.backoff_max_seconds == 600
    assert plugin.get_state() == {}


@pytest.mark.asyncio
async def test_under_threshold_passes_through() -> None:
    """2. Failures below the threshold do NOT block subsequent calls."""
    plugin, clock = _make_plugin({"default": {"max_failures": 3, "window_seconds": 60}})

    # Record two failures and verify breaker is still CLOSED / passing.
    for _ in range(2):
        await plugin.post_tool_execute(_fail(), tool_name="bash")
        clock.advance(1.0)

    # pre_tool_execute must not raise — threshold not breached yet.
    out = await plugin.pre_tool_execute({}, tool_name="bash")
    assert out is None

    state = plugin.get_state()["bash"]
    assert state["state"] == STATE_CLOSED
    assert state["count"] == 2


@pytest.mark.asyncio
async def test_threshold_breach_blocks_next_pre_call() -> None:
    """3. Crossing the threshold opens the breaker and blocks pre-hook."""
    plugin, clock = _make_plugin(
        {"default": {"max_failures": 3, "window_seconds": 60, "cooldown_seconds": 30}}
    )

    for _ in range(3):
        await plugin.post_tool_execute(_fail(), tool_name="bash")
        clock.advance(0.5)

    # Breaker must be OPEN now, the NEXT pre must raise PluginBlockError.
    state = plugin.get_state()["bash"]
    assert state["state"] == STATE_OPEN
    assert state["cooldown_remaining"] > 0

    with pytest.raises(PluginBlockError) as exc_info:
        await plugin.pre_tool_execute({}, tool_name="bash")
    msg = str(exc_info.value)
    assert "circuit breaker open" in msg
    assert "bash" in msg
    assert "cool-down" in msg

    # Unrelated tool still allowed.
    assert await plugin.pre_tool_execute({}, tool_name="web_fetch") is None


@pytest.mark.asyncio
async def test_cooldown_expiry_half_open_trial_allowed() -> None:
    """4. After cool-down expires the breaker flips to HALF_OPEN and
    allows a single trial call."""
    plugin, clock = _make_plugin(
        {"default": {"max_failures": 2, "cooldown_seconds": 10, "window_seconds": 60}}
    )

    # Trip the breaker.
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    assert plugin.get_state()["bash"]["state"] == STATE_OPEN

    # Before cooldown expires: blocked.
    clock.advance(5.0)
    with pytest.raises(PluginBlockError):
        await plugin.pre_tool_execute({}, tool_name="bash")

    # Advance past cooldown — next pre should transition to HALF_OPEN
    # and not raise.
    clock.advance(10.0)  # total elapsed 15s > 10s cooldown
    out = await plugin.pre_tool_execute({}, tool_name="bash")
    assert out is None
    assert plugin.get_state()["bash"]["state"] == STATE_HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_success_closes_and_failure_doubles_cooldown() -> None:
    """5. HALF_OPEN success -> CLOSED; HALF_OPEN failure -> OPEN with
    cool-down doubled (bounded by backoff_max_seconds)."""
    plugin, clock = _make_plugin(
        {
            "default": {
                "max_failures": 2,
                "cooldown_seconds": 10,
                "window_seconds": 60,
                "backoff_max_seconds": 60,
            }
        }
    )

    # ── Branch A: HALF_OPEN success closes the breaker ──
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    clock.advance(11.0)
    await plugin.pre_tool_execute({}, tool_name="bash")  # -> HALF_OPEN
    assert plugin.get_state()["bash"]["state"] == STATE_HALF_OPEN

    await plugin.post_tool_execute(_ok(), tool_name="bash")
    state = plugin.get_state()["bash"]
    assert state["state"] == STATE_CLOSED
    assert state["count"] == 0
    assert state["open_count"] == 0

    # ── Branch B: HALF_OPEN failure re-opens with doubled cooldown ──
    # Re-trip.
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    opened = plugin.get_state()["bash"]
    assert opened["state"] == STATE_OPEN
    first_cooldown = opened["current_cooldown"]
    assert first_cooldown == pytest.approx(10.0)

    # Wait out cooldown; half-open trial; fail it.
    clock.advance(first_cooldown + 1.0)
    await plugin.pre_tool_execute({}, tool_name="bash")  # -> HALF_OPEN
    assert plugin.get_state()["bash"]["state"] == STATE_HALF_OPEN

    await plugin.post_tool_execute(_fail(), tool_name="bash")
    reopened = plugin.get_state()["bash"]
    assert reopened["state"] == STATE_OPEN
    # Doubled.
    assert reopened["current_cooldown"] == pytest.approx(min(20.0, 60.0))

    # One more cycle to exercise the cap.
    clock.advance(reopened["current_cooldown"] + 1.0)
    await plugin.pre_tool_execute({}, tool_name="bash")  # HALF_OPEN
    await plugin.post_tool_execute(_fail(), tool_name="bash")  # re-open again
    capped = plugin.get_state()["bash"]["current_cooldown"]
    assert capped == pytest.approx(40.0)  # 20 -> 40 still under cap
    clock.advance(capped + 1.0)
    await plugin.pre_tool_execute({}, tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    # Next doubling would be 80 but cap is 60.
    assert plugin.get_state()["bash"]["current_cooldown"] == pytest.approx(60.0)


# ── Extra coverage ──


@pytest.mark.asyncio
async def test_reset_clears_breakers() -> None:
    plugin, _ = _make_plugin({"default": {"max_failures": 1}})
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    assert plugin.get_state()["bash"]["state"] == STATE_OPEN

    plugin.reset("bash")
    assert "bash" not in plugin.get_state()

    # reset() with no arg clears everything.
    await plugin.post_tool_execute(_fail(), tool_name="x")
    await plugin.post_tool_execute(_fail(), tool_name="y")
    plugin.reset()
    assert plugin.get_state() == {}


@pytest.mark.asyncio
async def test_sliding_window_prunes_old_failures() -> None:
    """Failures outside the window do not count toward the threshold."""
    plugin, clock = _make_plugin({"default": {"max_failures": 3, "window_seconds": 10}})
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    clock.advance(15.0)  # older failures age out
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    # Breaker should still be CLOSED; only 1 live failure.
    state = plugin.get_state()["bash"]
    assert state["state"] == STATE_CLOSED
    assert state["count"] == 1


@pytest.mark.asyncio
async def test_disabled_plugin_is_inert() -> None:
    plugin, _ = _make_plugin({"enabled": False, "default": {"max_failures": 1}})
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    # No state recorded because should_apply() short-circuits.
    assert plugin.get_state() == {}
    # And pre never blocks.
    assert await plugin.pre_tool_execute({}, tool_name="bash") is None


@pytest.mark.asyncio
async def test_nonzero_exit_counts_as_failure() -> None:
    plugin, _ = _make_plugin({"default": {"max_failures": 2}})
    bad = ToolResult(output="nope", exit_code=1)
    await plugin.post_tool_execute(bad, tool_name="bash")
    await plugin.post_tool_execute(bad, tool_name="bash")
    assert plugin.get_state()["bash"]["state"] == STATE_OPEN


@pytest.mark.asyncio
async def test_success_in_closed_clears_window() -> None:
    """Consecutive-failures intuition: a single success clears the
    sliding window while CLOSED."""
    plugin, _ = _make_plugin({"default": {"max_failures": 3}})
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_ok(), tool_name="bash")
    state = plugin.get_state()["bash"]
    assert state["state"] == STATE_CLOSED
    assert state["count"] == 0


@pytest.mark.asyncio
async def test_half_open_trial_disabled_just_closes_after_cooldown() -> None:
    plugin, clock = _make_plugin(
        {
            "half_open_trial": False,
            "default": {"max_failures": 2, "cooldown_seconds": 5},
        }
    )
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    await plugin.post_tool_execute(_fail(), tool_name="bash")
    assert plugin.get_state()["bash"]["state"] == STATE_OPEN
    clock.advance(6.0)
    out = await plugin.pre_tool_execute({}, tool_name="bash")
    assert out is None
    assert plugin.get_state()["bash"]["state"] == STATE_CLOSED
