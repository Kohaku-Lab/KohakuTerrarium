"""Connection-level hosted-tool compatibility for Responses WebSocket sessions."""

import asyncio

import pytest

from kohakuterrarium.builtins.tools.image_gen import ImageGenTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm import responses_ws, responses_ws_recovery
from kohakuterrarium.llm.recovery import RetryPolicy
from kohakuterrarium.llm.responses_ws import (
    ResponsesWSError,
    gated_tool_types,
    rejects_hosted_tool,
)
from kohakuterrarium.llm.responses_ws_recovery import WSRecovery
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.modules.subagent.model_resolve import resolve_subagent_llm
from tests.unit.llm.test_codex_provider import _FakeWSConnection, _FakeWSManager
from tests.unit.llm.test_openai_ws import MESSAGES
from tests.unit.llm.test_responses_ws import (
    ASSIST1,
    USER1,
    USER2,
    Ev,
    FakeConnection,
    Harness,
    completed,
)

FUNC = {"type": "function", "name": "read", "parameters": {}}
IMAGE = {"type": "image_generation"}
PLAIN = {"model": "m", "tools": [FUNC]}
HOSTED = {"model": "m", "tools": [FUNC, IMAGE]}
REJECTION = (
    "Hosted tool 'image_generation' requires authorization and metering "
    "that are not supported by rustponsesapi."
)
# Wire shape recorded from the live upstream rejection.
UPSTREAM_REJECTION = Ev(
    type="error",
    status=400,
    error={
        "code": "unsupported_parameter",
        "type": "invalid_request_error",
        "message": REJECTION,
        "param": "tools",
    },
)


def scripted(*responses):
    connection = FakeConnection()
    connection.scripts = [[event] for event in responses]
    return connection


def test_gated_tool_types_reads_only_gated_hosted_tools():
    assert gated_tool_types(HOSTED) == {"image_generation"}
    assert gated_tool_types(PLAIN) == frozenset()
    assert gated_tool_types({"model": "m"}) == frozenset()
    assert gated_tool_types({"tools": [{"type": "web_search"}]}) == frozenset()


def test_rejects_hosted_tool_requires_code_and_message():
    assert rejects_hosted_tool("unsupported_parameter", REJECTION)
    assert not rejects_hosted_tool("unsupported_parameter", "Unknown field 'x'")
    assert not rejects_hosted_tool("invalid_request_error", REJECTION)


class TestConnectionCapability:
    async def test_adding_gated_tool_reconnects_before_send(self):
        h = Harness()
        h.connections = [scripted(completed("r1")), scripted(completed("r2"))]
        await h.run([USER1], base=PLAIN)
        h.session.record_assistant_echo([ASSIST1])
        history = [USER1, ASSIST1, USER2]
        await h.run(history, base=HOSTED)
        first, second = h.connections
        assert h.factory_calls == 2
        assert first.closed and len(first.sent) == 1
        assert second.sent[0]["tools"] == [FUNC, IMAGE]
        assert second.sent[0]["input"] == ["PAIRED", *history]
        assert "previous_response_id" not in second.sent[0]

    async def test_hosted_first_connection_serves_plain_and_hosted_again(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")], [completed("r3")]]
        await h.run([USER1], base=HOSTED)
        h.session.record_assistant_echo([ASSIST1])
        await h.run([USER1, ASSIST1, USER2], base=PLAIN)
        h.session.record_assistant_echo([ASSIST1])
        await h.run([USER1, ASSIST1, USER2, ASSIST1, USER1], base=HOSTED)
        assert h.factory_calls == 1 and not h.conn.closed
        assert [e.get("previous_response_id") for e in h.conn.sent] == [
            None,
            "r1",
            "r2",
        ]

    async def test_plain_only_requests_never_reconnect(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")]]
        await h.run([USER1], base=PLAIN)
        await h.run([USER1], base={"model": "m"})
        assert h.factory_calls == 1

    async def test_capability_resets_when_the_socket_is_replaced(self):
        h = Harness()
        h.connections = [
            scripted(completed("r1")),
            scripted(completed("r2"), completed("stale")),
            scripted(completed("r3")),
        ]
        await h.run([USER1], base=HOSTED)
        h.connections[0].state = Ev(name="CLOSED")
        await h.run([USER1], base=PLAIN)
        await h.run([USER1], base=HOSTED)
        assert h.factory_calls == 3
        assert h.connections[1].closed
        assert h.connections[2].sent[0]["tools"] == [FUNC, IMAGE]

    async def test_capability_resets_after_mid_request_reconnect(self):
        h = Harness()
        dropped = FakeConnection()
        dropped.iter_exc = ConnectionError("lost")
        h.connections = [
            scripted(completed("r1")),
            dropped,
            scripted(completed("r2"), completed("stale")),
            scripted(completed("r3")),
        ]
        await h.run([USER1], base=HOSTED)
        h.connections[0].state = Ev(name="CLOSED")
        await h.run([USER1], base=PLAIN)
        await h.run([USER1], base=HOSTED)
        assert h.factory_calls == 4
        assert h.connections[2].closed
        assert h.connections[3].sent[0]["tools"] == [FUNC, IMAGE]

    async def test_explicit_close_forgets_capability(self):
        h = Harness()
        h.connections = [scripted(completed("r1")), scripted(completed("r2"))]
        await h.run([USER1], base=PLAIN)
        await h.session.close()
        await h.run([USER1], base=HOSTED)
        assert h.factory_calls == 2
        assert len(h.connections[1].sent) == 1


class TestHostedToolRejection:
    @pytest.mark.parametrize("shape", ["error", "failed"])
    async def test_rejection_retires_connection_and_replays(self, shape):
        h = Harness()
        if shape == "error":
            event = UPSTREAM_REJECTION
        else:
            event = Ev(
                type="response.failed",
                response=Ev(error=Ev(code="unsupported_parameter", message=REJECTION)),
            )
        h.connections = [scripted(event), scripted(completed("r1"))]
        events = await h.run([USER1], base=HOSTED)
        assert events[-1].response.id == "r1"
        assert h.factory_calls == 2 and h.connections[0].closed

    async def test_unreplayable_rejection_still_retires_connection(self):
        h = Harness()
        forbid = {"model": "m", "tools": [{**IMAGE, "request_replay": "forbid"}]}
        event = Ev(
            type="error", error=Ev(code="unsupported_parameter", message=REJECTION)
        )
        h.connections = [scripted(event), scripted(completed("r1"))]
        with pytest.raises(ResponsesWSError, match="Hosted tool"):
            await h.run([USER1], base=forbid)
        assert h.connections[0].closed
        events = await h.run([USER1], base=forbid)
        assert events[-1].response.id == "r1"
        assert h.factory_calls == 2

    @pytest.mark.parametrize("raw_delivery", [False, True])
    @pytest.mark.parametrize("shape", ["error", "failed"])
    async def test_rejection_after_hosted_tool_executed_does_not_replay(
        self, shape, raw_delivery
    ):
        image_call = Ev(
            type="response.output_item.done",
            item=Ev(type="image_generation_call", id="ig1"),
        )
        if shape == "error":
            rejection = UPSTREAM_REJECTION
        else:
            rejection = Ev(
                type="response.failed",
                response=Ev(error=Ev(code="unsupported_parameter", message=REJECTION)),
            )
        h = Harness()
        h.connections = [scripted(completed("unused")), scripted(completed("r1"))]
        h.connections[0].scripts = [[image_call, rejection]]
        # Provider paths count only text as delivered, so raw_delivery=False.
        recovery = WSRecovery(
            RetryPolicy(max_retries=3, base_delay=0), raw_delivery=raw_delivery
        )
        with pytest.raises(ResponsesWSError, match="Hosted tool"):
            async for _ in h.session.stream_turn(
                {"model": "m", **HOSTED}, [USER1], lambda x: x, recovery=recovery
            ):
                pass
        assert h.connections[0].closed
        assert h.factory_calls == 1 and recovery.submissions == 1

    async def test_other_unsupported_parameter_keeps_connection(self):
        h = Harness()
        event = Ev(
            type="error",
            error=Ev(code="unsupported_parameter", message="Unknown field 'x'"),
        )
        h.conn.scripts = [[event], [completed("r1")]]
        with pytest.raises(ResponsesWSError):
            await h.run([USER1], base=HOSTED)
        assert not h.conn.closed
        await h.run([USER1], base=HOSTED)
        assert h.factory_calls == 1


class _SlowCloseConnection(FakeConnection):
    """Close handshake that blocks until released, with a cancellable drain."""

    def __init__(self):
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_allowed = asyncio.Event()
        self.drain_cancelled_after_release: list[bool] = []

    async def recv_bytes(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.drain_cancelled_after_release.append(self.close_allowed.is_set())
            raise

    async def close(self):
        self.close_started.set()
        await self.close_allowed.wait()
        await super().close()


class TestCancelSafeReconnect:
    async def test_single_cancel_during_proactive_close_exits_promptly(
        self, monkeypatch
    ):
        monkeypatch.setattr(responses_ws_recovery, "CANCEL_CLOSE_GRACE", 0.05)
        old_conn = _SlowCloseConnection()
        old_conn.scripts = [[completed("r1")]]
        h = Harness()
        h.connections = [old_conn, scripted(completed("r2"))]
        await h.run([USER1], base=PLAIN)

        task = asyncio.create_task(h.run([USER1], base=HOSTED))
        await old_conn.close_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert not h.session.busy and h.session._connection is None
        assert len(h.session._closing) == 1 and not old_conn.closed
        assert old_conn.drain_cancelled_after_release == []

        old_conn.close_allowed.set()
        await h.session.close()
        assert old_conn.closed and not h.session._closing
        assert old_conn.drain_cancelled_after_release == [True]
        events = await h.run([USER1], base=HOSTED)
        assert events[-1].response.id == "r2"

    async def test_waiting_close_is_bounded_and_keeps_ownership(self, monkeypatch):
        monkeypatch.setattr(responses_ws, "SHUTDOWN_CLOSE_TIMEOUT", 0.05)
        conn = _SlowCloseConnection()
        conn.scripts = [[completed("r1")]]
        h = Harness()
        h.connections = [conn]
        await h.run([USER1], base=PLAIN)
        await asyncio.wait_for(h.session.close(), timeout=2)
        assert len(h.session._closing) == 1 and not conn.closed
        conn.close_allowed.set()
        await h.session.close(grace=1)
        assert conn.closed and not h.session._closing


class _UpstreamConnection(_FakeWSConnection):
    """Rejects image_generation unless the connection's first request declared it."""

    def __init__(self):
        super().__init__()
        self.first_tools: frozenset[str] | None = None
        self.pending: list[frozenset[str]] = []

    async def send(self, event):
        await super().send(event)
        tools = gated_tool_types(event)
        if self.first_tools is None:
            self.first_tools = tools
        self.pending.append(tools)

    def __aiter__(self):
        tools = self.pending.pop(0)
        if not tools <= self.first_tools:
            self.scripts.insert(0, [UPSTREAM_REJECTION])
        return super().__aiter__()


class _ConnectionPool:
    """responses namespace handing out a fresh upstream connection per connect()."""

    def __init__(self):
        self.connections: list[_UpstreamConnection] = []

    def connect(self, **kwargs):
        connection = _UpstreamConnection()
        connection.scripts = [[completed(f"r{i}")] for i in range(4)]
        self.connections.append(connection)
        return _FakeWSManager(connection)


class _PoolClient:
    def __init__(self):
        self.responses = _ConnectionPool()

    def with_options(self, **kwargs):
        return self


def _codex_provider():
    provider = CodexOAuthProvider(
        api_key="test",
        websocket_mode=True,
        retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
    )
    provider._client = _PoolClient()
    return provider


async def test_shared_codex_provider_worker_then_controller():
    provider = _codex_provider()
    pool = provider._client.responses
    _ = [x async for x in provider.chat(MESSAGES)]
    _ = [
        x async for x in provider.chat(MESSAGES, provider_native_tools=[ImageGenTool()])
    ]
    worker, controller = pool.connections
    assert worker.closed and len(worker.sent) == 1
    assert "tools" not in worker.sent[0]
    assert controller.sent[0]["tools"][0]["type"] == "image_generation"
    _ = [x async for x in provider.chat(MESSAGES)]
    assert len(pool.connections) == 2 and len(controller.sent) == 2


async def test_inherited_subagent_on_fresh_connection_then_controller():
    parent = _codex_provider()
    pool = parent._client.responses
    registry = Registry()
    config = SubAgentConfig(
        name="worker",
        system_prompt="Reply OK.",
        model="inherit",
        tool_format="native",
        max_turns=1,
    )
    assert resolve_subagent_llm(parent, config) is parent
    manager = SubAgentManager(registry, parent, tool_format="native")
    manager.register(config)

    async def controller_turn():
        return [
            x
            async for x in parent.chat(MESSAGES, provider_native_tools=[ImageGenTool()])
        ]

    async def worker_turn():
        job_id = await manager.spawn("worker", "go", background=True)
        assert manager._jobs[job_id].subagent.llm is parent
        result = await manager.wait_for(job_id, timeout=10)
        assert result is not None and result.success, result

    await controller_turn()
    await worker_turn()
    await parent._reset_ws_session()
    await worker_turn()
    await controller_turn()
    first, worker_conn, controller_conn = pool.connections
    assert all("image_generation" not in str(e.get("tools")) for e in worker_conn.sent)
    assert worker_conn.closed
    assert gated_tool_types(controller_conn.sent[0]) == {"image_generation"}
    assert len(controller_conn.sent) == 1
