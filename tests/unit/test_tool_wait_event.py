"""Wave B — ``tool_wait`` additive event.

Two concurrency-unsafe tools launched concurrently must serialize on
the shared lock, and the second one should emit a ``tool_wait``
activity carrying ``wait_ms`` / ``reason="serial_lock"``.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.executor import Executor
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


class _SlowUnsafeTool(BaseTool):
    """Unsafe tool that blocks for ``hold_s`` seconds."""

    is_concurrency_safe = False

    def __init__(self, name: str, hold_s: float):
        super().__init__()
        self._name = name
        self._hold_s = hold_s

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "blocks for a while"

    async def execute(self, args, context=None) -> ToolResult:
        await asyncio.sleep(self._hold_s)
        return ToolResult(output="done", exit_code=0)


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "tool_wait.kohakutr")
    s.init_meta(
        session_id="tool_wait",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


class _RecordingRouter:
    """Stand-in output router: records notify_activity() calls."""

    def __init__(self) -> None:
        self.activities: list[tuple[str, str, dict]] = []

    def notify_activity(self, activity_type, detail, metadata=None):
        self.activities.append((activity_type, detail, metadata or {}))


class TestToolWaitEmission:
    @pytest.mark.asyncio
    async def test_second_unsafe_tool_emits_tool_wait(self):
        """Run one slow unsafe tool; a concurrent second one should wait."""
        router = _RecordingRouter()
        agent_stub = SimpleNamespace(output_router=router)
        executor = Executor()
        executor._agent = agent_stub
        slow = _SlowUnsafeTool("slow_unsafe_a", hold_s=0.1)
        fast = _SlowUnsafeTool("slow_unsafe_b", hold_s=0.01)
        executor.register_tool(slow)
        executor.register_tool(fast)

        j1 = await executor.submit("slow_unsafe_a", {}, is_direct=True)
        j2 = await executor.submit("slow_unsafe_b", {}, is_direct=True)
        # Block on both jobs so the serial lock gate trips.
        await executor.wait_for(j1)
        await executor.wait_for(j2)
        # Let any pending callbacks settle.
        await asyncio.sleep(0.05)

        tool_waits = [a for a in router.activities if a[0] == "tool_wait"]
        assert tool_waits, f"expected at least one tool_wait, got {router.activities}"
        _type, _detail, metadata = tool_waits[-1]
        assert metadata["tool"] in {"slow_unsafe_a", "slow_unsafe_b"}
        assert metadata["reason"] == "serial_lock"
        assert metadata["wait_ms"] >= 1.0


class TestToolWaitHandlerStoresEvent:
    def test_activity_round_trips_into_session_event(self, store):
        """SessionOutput maps the activity to a ``tool_wait`` event row."""
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)

        output.on_activity_with_metadata(
            "tool_wait",
            "[bash] waited 12.3ms on serial_lock",
            {"tool": "bash", "wait_ms": 12.3, "reason": "serial_lock"},
        )

        evts = [e for e in store.get_events("agent") if e["type"] == "tool_wait"]
        assert len(evts) == 1
        assert evts[0]["tool"] == "bash"
        assert evts[0]["wait_ms"] == pytest.approx(12.3)
        assert evts[0]["reason"] == "serial_lock"
