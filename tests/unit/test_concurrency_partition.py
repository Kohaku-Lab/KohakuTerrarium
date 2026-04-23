"""Tests for Cluster 5 / G.1 — concurrency-safe tool partitioning.

Covers:
- Tools default to ``is_concurrency_safe = True``.
- Built-in write / edit / multi_edit / bash flip to False.
- The executor partitions a batch:
    - All-safe batch runs in parallel (concurrent timing).
    - All-unsafe batch runs serially (timing-detectable).
    - Mixed batches only serialize the unsafe half.
- Two unsafe tools never run concurrently (asyncio lock holds).
"""

import asyncio
import time
from typing import Any

import pytest

from kohakuterrarium.builtins.tools.bash import ShellTool
from kohakuterrarium.builtins.tools.edit import EditTool
from kohakuterrarium.builtins.tools.multi_edit import MultiEditTool
from kohakuterrarium.builtins.tools.write import WriteTool
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

# ---------------------------------------------------------------------------
# Timing-sensitive fake tools
# ---------------------------------------------------------------------------


class _SleepyTool(BaseTool):
    """Sleeps for ``duration`` seconds and records its concurrency window."""

    def __init__(
        self,
        name: str,
        *,
        duration: float = 0.05,
        safe: bool,
        tracker: dict,
    ):
        super().__init__()
        self._name = name
        self._duration = duration
        self.is_concurrency_safe = safe
        # Tracker shared across all fake tools in a test, used to
        # record (start, end) timestamps per invocation id.
        self._tracker = tracker
        # active_unsafe tracks how many unsafe tools are currently
        # inside their sleep window. Must stay <= 1 at all times for a
        # correctly-serialized batch.
        self._tracker.setdefault("active_unsafe", 0)
        self._tracker.setdefault("max_active_unsafe", 0)
        self._tracker.setdefault("events", [])

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "sleeper"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        invocation_id = args.get("id", self._name)
        if not self.is_concurrency_safe:
            self._tracker["active_unsafe"] += 1
            self._tracker["max_active_unsafe"] = max(
                self._tracker["max_active_unsafe"], self._tracker["active_unsafe"]
            )
        start = time.monotonic()
        try:
            await asyncio.sleep(self._duration)
        finally:
            end = time.monotonic()
            self._tracker["events"].append(
                {
                    "tool": self._name,
                    "id": invocation_id,
                    "start": start,
                    "end": end,
                    "safe": self.is_concurrency_safe,
                }
            )
            if not self.is_concurrency_safe:
                self._tracker["active_unsafe"] -= 1
        return ToolResult(output=f"{self._name}/{invocation_id}", exit_code=0)


# ---------------------------------------------------------------------------
# Built-in flip check
# ---------------------------------------------------------------------------


class TestBuiltinConcurrencyFlags:
    def test_write_is_unsafe(self):
        assert WriteTool.is_concurrency_safe is False

    def test_edit_is_unsafe(self):
        assert EditTool.is_concurrency_safe is False

    def test_multi_edit_is_unsafe(self):
        assert MultiEditTool.is_concurrency_safe is False

    def test_bash_is_unsafe(self):
        assert ShellTool.is_concurrency_safe is False

    def test_basetool_default_is_safe(self):
        class _AnyTool(BaseTool):
            @property
            def tool_name(self) -> str:
                return "any"

            @property
            def description(self) -> str:
                return "any"

            async def _execute(self, args, **kwargs):
                return ToolResult(output="")

        assert _AnyTool.is_concurrency_safe is True


# ---------------------------------------------------------------------------
# Executor partitioning — timing assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_safe_batch_runs_parallel():
    tracker: dict = {}
    executor = Executor()
    safe_a = _SleepyTool("safe_a", duration=0.05, safe=True, tracker=tracker)
    safe_b = _SleepyTool("safe_b", duration=0.05, safe=True, tracker=tracker)
    executor.register_tool(safe_a)
    executor.register_tool(safe_b)

    wall_start = time.monotonic()
    job_a = await executor.submit("safe_a", {"id": "1"}, is_direct=True)
    job_b = await executor.submit("safe_b", {"id": "2"}, is_direct=True)
    await executor.wait_for(job_a)
    await executor.wait_for(job_b)
    elapsed = time.monotonic() - wall_start

    # Parallel: elapsed should be much closer to 0.05 than 0.10.
    assert elapsed < 0.09, f"safe batch did not parallelize (elapsed={elapsed:.3f})"


@pytest.mark.asyncio
async def test_all_unsafe_batch_runs_serial():
    tracker: dict = {}
    executor = Executor()
    unsafe_a = _SleepyTool("unsafe_a", duration=0.05, safe=False, tracker=tracker)
    unsafe_b = _SleepyTool("unsafe_b", duration=0.05, safe=False, tracker=tracker)
    executor.register_tool(unsafe_a)
    executor.register_tool(unsafe_b)

    wall_start = time.monotonic()
    job_a = await executor.submit("unsafe_a", {"id": "1"}, is_direct=True)
    job_b = await executor.submit("unsafe_b", {"id": "2"}, is_direct=True)
    await executor.wait_for(job_a)
    await executor.wait_for(job_b)
    elapsed = time.monotonic() - wall_start

    assert elapsed >= 0.09, (
        f"unsafe batch must serialize (elapsed={elapsed:.3f}, " "expected >= 0.09)"
    )
    # And critically: the two unsafe tools never overlap.
    assert tracker["max_active_unsafe"] == 1, (
        f"two unsafe tools ran concurrently "
        f"(max_active={tracker['max_active_unsafe']})"
    )


@pytest.mark.asyncio
async def test_mixed_batch_serializes_only_unsafe():
    tracker: dict = {}
    executor = Executor()
    unsafe_a = _SleepyTool("unsafe_a", duration=0.05, safe=False, tracker=tracker)
    unsafe_b = _SleepyTool("unsafe_b", duration=0.05, safe=False, tracker=tracker)
    safe_x = _SleepyTool("safe_x", duration=0.05, safe=True, tracker=tracker)
    safe_y = _SleepyTool("safe_y", duration=0.05, safe=True, tracker=tracker)
    for t in (unsafe_a, unsafe_b, safe_x, safe_y):
        executor.register_tool(t)

    wall_start = time.monotonic()
    # Kick off all four concurrently — only unsafe_a/unsafe_b must
    # serialize against each other.
    jobs = await asyncio.gather(
        executor.submit("unsafe_a", {"id": "1"}, is_direct=True),
        executor.submit("unsafe_b", {"id": "2"}, is_direct=True),
        executor.submit("safe_x", {"id": "3"}, is_direct=True),
        executor.submit("safe_y", {"id": "4"}, is_direct=True),
    )
    for jid in jobs:
        await executor.wait_for(jid)
    elapsed = time.monotonic() - wall_start

    # Two unsafe tools in series = 0.10, run in parallel with safe tools.
    # Expect ~0.10 total — not ~0.20 (full serial) or ~0.05 (fully parallel).
    assert 0.09 <= elapsed < 0.18, (
        f"mixed batch timing off (elapsed={elapsed:.3f}, "
        "expected ~0.10 — parallel safe, serial unsafe)"
    )
    assert tracker["max_active_unsafe"] == 1


@pytest.mark.asyncio
async def test_serial_lock_is_shared_not_per_tool():
    """Even different unsafe tools must share one serial lock."""
    tracker: dict = {}
    executor = Executor()
    # Three different unsafe tool classes — they all serialize.
    for name in ("u1", "u2", "u3"):
        executor.register_tool(
            _SleepyTool(name, duration=0.03, safe=False, tracker=tracker)
        )

    jobs = await asyncio.gather(
        executor.submit("u1", {"id": "1"}, is_direct=True),
        executor.submit("u2", {"id": "2"}, is_direct=True),
        executor.submit("u3", {"id": "3"}, is_direct=True),
    )
    for jid in jobs:
        await executor.wait_for(jid)

    assert tracker["max_active_unsafe"] == 1
    # 3 × 0.03 ≈ 0.09 minimum for strict serialization.
    durations = [e["end"] - e["start"] for e in tracker["events"]]
    assert sum(durations) >= 0.08
