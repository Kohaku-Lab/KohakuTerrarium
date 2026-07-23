"""Unit tests for ``llm/responses_ws.py`` incremental WebSocket sessions."""

import pytest

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
        self.scripts = []
        self.send_exc = None
        self.iter_exc = None
        self.closed = False

    async def send(self, event):
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

        await h.run([USER1, ASSIST1, CALL1, OUT1, USER2])

        sent = h.conn.sent[1]
        assert sent["previous_response_id"] == "r1"
        # The assistant echo and the server's own function_call are dropped;
        # only the tool output and the new user message travel.
        assert sent["input"] == [OUT1, USER2]

    async def test_edited_history_falls_back_to_full_resend(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")], [completed("r2")]]
        await h.run([USER1])

        edited = {"role": "user", "content": [{"type": "input_text", "text": "EDIT"}]}
        await h.run([edited, USER2])

        sent = h.conn.sent[1]
        assert "previous_response_id" not in sent
        assert sent["input"] == ["PAIRED", edited, USER2]

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
    async def test_cache_miss_resends_full_on_same_connection(self):
        h = Harness()
        h.conn.scripts = [
            [completed("r1")],
            [error("previous_response_not_found")],
            [text("B"), completed("r2")],
        ]
        await h.run([USER1])

        events = await h.run([USER1, ASSIST1, USER2])

        assert h.conn.sent[1]["previous_response_id"] == "r1"
        assert "previous_response_id" not in h.conn.sent[2]
        assert h.conn.sent[2]["input"] == ["PAIRED", USER1, ASSIST1, USER2]
        assert [getattr(e, "type", "") for e in events] == [
            "response.output_text.delta",
            "response.completed",
        ]

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

    async def test_dead_connection_retries_full_on_fresh_connection(self):
        h = Harness()
        h.conn.scripts = [[completed("r1")]]
        await h.run([USER1])

        # Second turn: the old connection dies before any event arrives.
        h.conn.scripts = [[]]
        h.connections.append(FakeConnection())
        h.connections[1].scripts = [[text("C"), completed("r2")]]

        events = await h.run([USER1, ASSIST1, USER2])

        assert h.factory_calls == 2
        retry_sent = h.connections[1].sent[0]
        assert "previous_response_id" not in retry_sent
        assert retry_sent["input"] == ["PAIRED", USER1, ASSIST1, USER2]
        assert [getattr(e, "type", "") for e in events] == [
            "response.output_text.delta",
            "response.completed",
        ]

    async def test_mid_stream_failure_propagates_without_retry(self):
        h = Harness()
        h.conn.scripts = [[text("partial")]]
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
