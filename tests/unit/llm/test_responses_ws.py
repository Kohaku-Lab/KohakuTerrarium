"""Unit tests for ``llm/responses_ws.py`` incremental WebSocket sessions."""

import asyncio
import json

import pytest
from openai import AsyncOpenAI
from websockets import serve as serve_websocket
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close
from openai.resources.responses import responses as sdk_responses

from kohakuterrarium.llm.responses_ws import ResponsesWSError, ResponsesWSSession


class Ev:
    """Attribute-bag stand-in for SDK server events."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def completed(resp_id, call_ids=(), usage=None):
    output = [Ev(type="function_call", call_id=c) for c in call_ids]
    return Ev(
        type="response.completed",
        response=Ev(id=resp_id, output=output, usage=usage),
    )


def text(delta):
    return Ev(type="response.output_text.delta", delta=delta)


def error(code, message="boom"):
    return Ev(type="error", error=Ev(code=code, message=message))


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.send_attempts = 0
        self.scripts = []
        self.send_exc = None
        self.iter_exc = None
        self.closed = False

    async def send(self, event):
        self.send_attempts += 1
        if self.send_exc is not None:
            exc, self.send_exc = self.send_exc, None
            raise exc
        self.sent.append(event)

    def __aiter__(self):
        events = self.scripts.pop(0) if self.scripts else []
        iter_exc = self.iter_exc

        async def gen():
            for e in events:
                yield e
            if iter_exc is not None:
                raise iter_exc

        return gen()

    async def close(self):
        self.closed = True


class FakeManager:
    def __init__(self, connection):
        self.connection = connection

    async def enter(self):
        return self.connection


class Harness:
    """Session + factory bookkeeping for one test scenario."""

    def __init__(self):
        self.connections = [FakeConnection()]
        self.factory_calls = 0

        def factory():
            self.factory_calls += 1
            if self.factory_calls > len(self.connections):
                self.connections.append(FakeConnection())
            return FakeManager(self.connections[self.factory_calls - 1])

        self.session = ResponsesWSSession(factory)

    @property
    def conn(self):
        return self.connections[0]

    async def run(self, items, base=None, pairing=None):
        events = []
        async for event in self.session.stream_turn(
            base or {"model": "m"}, items, pairing or (lambda x: ["PAIRED", *x])
        ):
            events.append(event)
        return events


USER1 = {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
ASSIST1 = {"role": "assistant", "content": [{"type": "output_text", "text": "yo"}]}
CALL1 = {"type": "function_call", "call_id": "c1", "name": "t", "arguments": "{}"}
OUT1 = {"type": "function_call_output", "call_id": "c1", "output": "ok"}
USER2 = {"role": "user", "content": [{"type": "input_text", "text": "next"}]}


class TestFullAndIncrementalTurns:
    async def test_reasoning_echo_is_skipped_only_for_incremental_requests(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1", call_ids=("c1",))],
            [completed("r2")],
            [completed("r3")],
        ]
        reasoning = {
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "Inspect files"}],
        }
        await h.run([USER1])
        h.session.record_assistant_echo([reasoning, ASSIST1, CALL1])
        history = [USER1, reasoning, ASSIST1, CALL1, OUT1]
        await h.run(history)
        assert h.conn.sent[1]["input"] == [OUT1]
        assert h.conn.sent[1]["previous_response_id"] == "r1"
        h.session.invalidate()
        await h.run([*history, USER2])
        assert h.conn.sent[2]["input"] == ["PAIRED", *history, USER2]
        assert "previous_response_id" not in h.conn.sent[2]

    async def test_first_turn_sends_paired_full_input(self):
        h = Harness()
        h.conn.scripts = [[text("A"), completed("r1")]]

        events = await h.run([USER1])

        sent = h.conn.sent[0]
        assert sent["type"] == "response.create"
        assert sent["input"] == ["PAIRED", USER1]
        assert "previous_response_id" not in sent
        assert [getattr(e, "type", "") for e in events] == [
            "response.output_text.delta",
            "response.completed",
        ]

    async def test_second_turn_sends_delta_without_server_echoes(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1", call_ids=("c1",))],
            [completed("r2")],
        ]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1, CALL1])
        await h.run([USER1, ASSIST1, CALL1, OUT1, USER2])

        sent = h.conn.sent[1]
        assert sent["previous_response_id"] == "r1"
        # The assistant echo and the server's own function_call are dropped;
        # only the tool output and the new user message travel.
        assert sent["input"] == [OUT1, USER2]

    async def test_edited_history_falls_back_to_full_resend(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")]]
        user = {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        await h.run([user])
        h.session.record_assistant_echo([ASSIST1])

        user["content"][0]["text"] = "EDIT"
        await h.run([user, ASSIST1, USER2])

        sent = h.conn.sent[1]
        assert "previous_response_id" not in sent
        assert sent["input"] == ["PAIRED", user, ASSIST1, USER2]

    async def test_invalidate_forces_full_resend(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")]]
        await h.run([USER1])

        h.session.invalidate()
        await h.run([USER1, ASSIST1, USER2])

        sent = h.conn.sent[1]
        assert "previous_response_id" not in sent
        assert sent["input"] == ["PAIRED", USER1, ASSIST1, USER2]


class TestFailureRecovery:
    @pytest.mark.parametrize("side", ["sent", "received"])
    @pytest.mark.parametrize(
        "code,retry",
        [
            (1002, False),
            (1008, False),
            (1009, False),
            (4001, False),
            (1011, True),
            (1012, True),
        ],
    )
    async def test_explicit_close_code_controls_replay(self, side, code, retry):
        h = Harness()
        frame = Close(code, "synthetic close")
        h.conn.iter_exc = ConnectionClosedError(
            frame if side == "received" else None,
            frame if side == "sent" else None,
        )
        replacement = FakeConnection()
        replacement.scripts = [[completed("recovered")]]
        h.connections.append(replacement)
        if retry:
            events = await h.run([USER1])
            assert events[-1].response.id == "recovered"
        else:
            with pytest.raises(ResponsesWSError, match=str(code)):
                await h.run([USER1])
        assert h.factory_calls == (2 if retry else 1)

    @pytest.mark.parametrize(
        ("state", "closed", "reconnect"),
        [
            ("CLOSING", False, True),
            ("CLOSED", False, True),
            (None, True, True),
            ("OPEN", False, False),
        ],
    )
    async def test_socket_state_controls_pre_send_reconnection(
        self, state, closed, reconnect
    ):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")]]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1])
        h.conn.state = Ev(name=state)
        h.conn.closed = closed
        if reconnect:
            replacement = FakeConnection()
            replacement.scripts = [[completed("r2")]]
            h.connections.append(replacement)
        history = [USER1, ASSIST1, USER2]
        await h.run(history)
        if reconnect:
            assert h.factory_calls == 2 and h.conn.send_attempts == 1
            sent = h.connections[1].sent[0]
            assert sent["input"] == ["PAIRED", *history]
            assert "previous_response_id" not in sent
        else:
            assert h.factory_calls == 1
            assert h.conn.sent[1]["input"] == [USER2]
            assert h.conn.sent[1]["previous_response_id"] == "r1"
        await h.session.close()

    async def test_large_failed_send_reports_transport_cause_without_replay(self):
        connection_type = getattr(sdk_responses, "AsyncResponsesConnection", None)
        if connection_type is None:
            pytest.skip("Installed OpenAI SDK has no Responses WebSocket support")

        class FailingSocket:
            def __init__(self):
                self.attempts = 0
                self.closed = False

            async def send(self, data):
                self.attempts += 1
                raise ConnectionError("synthetic underlying send failure")

            async def recv(self, **kwargs):
                raise EOFError

            async def close(self, **kwargs):
                self.closed = True

        raw = FailingSocket()
        connection = connection_type(connection=raw)
        if getattr(connection, "_send_queue", None) is None:
            await connection.close()
            pytest.skip("Installed OpenAI SDK has no failed-send buffer")
        session = ResponsesWSSession(lambda: FakeManager(connection))
        history = [{"role": "user", "content": "x" * (1024**2 + 1)}]
        try:
            with pytest.raises(
                ResponsesWSError, match="synthetic underlying send failure"
            ) as captured:
                async for _ in session.stream_turn(
                    {"model": "m", "tools": [{"type": "image_generation"}]},
                    history,
                    lambda x: x,
                ):
                    pass
            assert captured.value.submitted and captured.value.transport
            assert not captured.value.mid_stream
            assert raw.attempts == 1 and raw.closed
            assert session._connection is None and session._prev_id is None
            assert (
                captured.value.__cause__.__class__.__name__ == "WebSocketQueueFullError"
            )
        finally:
            await session.close()

    async def test_real_sdk_closed_socket_reconnects_with_full_history_before_send(
        self,
    ):
        submissions = []

        async def serve(socket):
            submissions.append(json.loads(await socket.recv()))
            await socket.send(
                json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": f"r{len(submissions)}",
                            "status": "completed",
                            "output": [],
                        },
                    }
                )
            )
            if len(submissions) == 1:
                await socket.close(code=1001)
            else:
                await socket.wait_closed()

        async with serve_websocket(serve, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AsyncOpenAI(api_key="test", base_url=f"http://127.0.0.1:{port}/v1")
            if not hasattr(client.responses, "connect"):
                await client.close()
                pytest.skip("Installed OpenAI SDK has no Responses WebSocket support")
            session = ResponsesWSSession(lambda: client.responses.connect())
            try:
                async for _ in session.stream_turn(
                    {"model": "m"}, [USER1], lambda x: x
                ):
                    pass
                session.record_assistant_echo([ASSIST1])
                await session._connection._connection.wait_closed()
                history = [USER1, ASSIST1, USER2]
                events = [
                    event
                    async for event in session.stream_turn(
                        {"model": "m"}, history, lambda x: x
                    )
                ]
                assert events[-1].response.id == "r2"
                assert len(submissions) == 2
                assert submissions[1]["input"] == history
                assert "previous_response_id" not in submissions[1]
            finally:
                await session.close()
                await client.close()

    async def test_close_drains_buffered_frames_with_real_sdk(self):
        submissions = []
        burst_sent = asyncio.Event()

        async def serve(socket):
            submissions.append(json.loads(await socket.recv()))
            await socket.send(
                json.dumps({"type": "response.output_text.delta", "delta": "first"})
            )
            for _ in range(32):
                await socket.send("discard during close")
            burst_sent.set()
            await socket.wait_closed()

        async with serve_websocket(serve, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AsyncOpenAI(api_key="test", base_url=f"http://127.0.0.1:{port}/v1")
            if not hasattr(client.responses, "connect"):
                await client.close()
                pytest.skip("Installed OpenAI SDK has no Responses WebSocket support")
            session = ResponsesWSSession(
                lambda: client.responses.connect(
                    websocket_connection_options={"max_queue": 1, "close_timeout": 0.25}
                )
            )
            stream = session.stream_turn({"model": "m"}, [USER1], lambda x: x)
            try:
                assert (await anext(stream)).delta == "first"
                await burst_sent.wait()
                connection = session._connection
                await stream.aclose()
                assert connection._connection.close_code == 1000
                assert not session.busy and session._connection is None
                assert len(submissions) == 1
            finally:
                await stream.aclose()
                await session.close()
                await client.close()

    async def test_cache_miss_resends_full_on_same_connection(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1")],
            [error("previous_response_not_found")],
            [text("B"), completed("r2")],
        ]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1])
        events = await h.run([USER1, ASSIST1, USER2])

        assert h.conn.sent[1]["previous_response_id"] == "r1"
        assert "previous_response_id" not in h.conn.sent[2]
        assert h.conn.sent[2]["input"] == ["PAIRED", USER1, ASSIST1, USER2]
        assert [getattr(e, "type", "") for e in events] == [
            "response.output_text.delta",
            "response.completed",
        ]

    async def test_repeated_cache_miss_is_not_retried_again(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1")],
            [error("previous_response_not_found")],
            [error("previous_response_not_found")],
        ]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1])
        with pytest.raises(ResponsesWSError):
            await h.run([USER1, ASSIST1, USER2])
        assert len(h.conn.sent) == 3
        assert h.factory_calls == 1

    async def test_server_error_event_raises_and_invalidates(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1")],
            [error("some_other_error")],
            [completed("r2")],
        ]
        await h.run([USER1])

        with pytest.raises(ResponsesWSError) as exc_info:
            await h.run([USER1, ASSIST1, USER2])
        assert not exc_info.value.mid_stream

        # The failed turn evicted the cached response: next turn is full.
        await h.run([USER1, ASSIST1, USER2])
        assert "previous_response_id" not in h.conn.sent[2]

    @pytest.mark.parametrize("kind", ["response.failed", "response.incomplete"])
    async def test_unsuccessful_terminal_response_stops_without_waiting_for_eof(
        self, kind
    ):
        h = Harness()
        response = (
            Ev(error=Ev(message="generation stopped"))
            if kind == "response.failed"
            else Ev(incomplete_details=Ev(reason="max_output_tokens"))
        )
        reason = (
            "generation stopped" if kind == "response.failed" else "max_output_tokens"
        )
        h.conn.scripts = [
            [completed("r1")],
            [Ev(type=kind, response=response)],
            [completed("r2")],
        ]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1])
        with pytest.raises(ResponsesWSError, match=reason) as exc:
            await h.run([USER1, ASSIST1, USER2])
        assert not exc.value.transport
        assert exc.value.submitted
        assert not h.conn.closed and not h.session.busy
        assert h.session._prev_id is None
        assert len(h.conn.sent) == 2
        await h.run([USER1, ASSIST1, USER2])
        assert "previous_response_id" not in h.conn.sent[2]

    async def test_disconnect_before_events_reconnects_once_with_full_history(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")]]
        await h.run([USER1])
        h.session.record_assistant_echo([ASSIST1])

        h.conn.scripts = [[]]
        h.connections.append(FakeConnection())
        h.connections[1].scripts = [[text("C"), completed("r2")]]

        events = await h.run([USER1, ASSIST1, USER2])

        assert h.factory_calls == 2
        assert [event.type for event in events] == [
            "response.output_text.delta",
            "response.completed",
        ]
        sent = h.connections[1].sent[0]
        assert h.conn.sent[1]["previous_response_id"] == "r1"
        assert h.conn.sent[1]["input"] == [USER2]
        assert sent["input"] == ["PAIRED", USER1, ASSIST1, USER2]
        assert "previous_response_id" not in sent
        assert h.conn.closed
        assert h.session._prev_id == "r2"

    async def test_send_failure_before_events_recovers_once(self):
        h = Harness()
        h.conn.send_exc = ConnectionError("write interrupted")
        recovered = FakeConnection()
        recovered.scripts = [[completed("r2")]]
        h.connections.append(recovered)
        await h.run(
            [USER1],
            base={"model": "m", "tools": [{"type": "function", "name": "read"}]},
        )
        assert sum(c.send_attempts for c in h.connections) == 2
        assert h.conn.closed
        assert h.session._prev_id == "r2"

    @pytest.mark.parametrize(
        "base",
        [
            {"tools": [{"type": "image_generation"}]},
            {
                "tools": [
                    {"type": "function", "name": "read"},
                    {"type": "image_generation"},
                ]
            },
            {"tools": [{"type": "mcp", "server_url": "https://example.invalid"}]},
            {"background": True},
        ],
    )
    async def test_server_executed_work_is_not_replayed(self, base):
        h = Harness()
        with pytest.raises(ResponsesWSError):
            await h.run([USER1], base={"model": "m", **base})
        assert h.factory_calls == 1 and h.conn.send_attempts == 1

    async def test_failed_reconnect_retains_prior_submission_uncertainty(self):
        h = Harness()
        await h.session._ensure_connection()

        def unavailable():
            raise ConnectionError("reconnect failed")

        h.session._connect_factory = unavailable
        with pytest.raises(ResponsesWSError, match="reconnect failed") as captured:
            await h.run([USER1])
        assert captured.value.submitted and not captured.value.mid_stream
        assert h.conn.send_attempts == 1 and h.conn.closed

    async def test_cancellation_closes_connection_and_clears_history(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], []]
        await h.run([USER1])
        h.conn.iter_exc = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await h.run([USER1, ASSIST1, USER2])
        assert h.conn.closed
        assert h.session._prev_id is None
        assert not h.session.busy

    async def test_generator_close_drops_inflight_connection(self):
        h = Harness()
        h.conn.scripts = [[text("partial")]]
        stream = h.session.stream_turn({"model": "m"}, [USER1], lambda x: x)
        await anext(stream)
        await stream.aclose()
        assert h.conn.closed
        assert h.session._prev_id is None
        assert not h.session.busy

    @pytest.mark.parametrize("event", [text("partial"), Ev(type="response.created")])
    async def test_mid_stream_failure_propagates_without_retry(self, event):
        h = Harness()
        h.conn.scripts = [[event]]
        h.conn.iter_exc = ConnectionError("socket dropped")

        collected = []
        with pytest.raises(ResponsesWSError) as exc_info:
            async for event in h.session.stream_turn(
                {"model": "m"}, [USER1], lambda x: x
            ):
                collected.append(event)

        assert exc_info.value.mid_stream
        assert len(collected) == 1
        assert h.factory_calls == 1  # no silent retry that would duplicate text

    async def test_busy_is_visible_while_turn_in_flight(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")]]
        gen = h.session.stream_turn({"model": "m"}, [USER1], lambda x: x)
        assert not h.session.busy
        await gen.__anext__()
        assert h.session.busy
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        assert not h.session.busy
