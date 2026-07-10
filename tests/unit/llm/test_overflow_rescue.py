"""Unit tests for the provider overflow-rescue hook (``_overflow_rescue``)."""

from kohakuterrarium.llm.base import ChatResponse
from kohakuterrarium.llm.openai import OpenAIProvider


class _OverflowErr(Exception):
    status_code = 413


_BIG = [
    {"role": "system", "content": "s"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "t1",
                "type": "function",
                "function": {"name": "x", "arguments": "{}"},
            }
        ],
    },
    {"role": "tool", "content": "r", "tool_call_id": "t1"},
]
_SMALL = [
    {"role": "system", "content": "s"},
    {"role": "assistant", "content": "[summary]"},
]
_MEDIUM = [
    {"role": "assistant", "content": "[summary]"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "t2",
                "type": "function",
                "function": {"name": "y", "arguments": "{}"},
            }
        ],
    },
    {"role": "tool", "content": "r2", "tool_call_id": "t2"},
]


class TestStreamOverflowRescue:
    async def test_rescue_consulted_before_dropping_tool_data(self, monkeypatch):
        provider = OpenAIProvider(api_key="k", model="m")
        seen: list[list] = []

        async def fake_raw(messages, **kwargs):
            seen.append(list(messages))
            if len(seen) == 1:
                raise _OverflowErr("context_length_exceeded")
            yield "ok"

        monkeypatch.setattr(provider, "_raw_stream_chat", fake_raw)
        rescued_calls = []

        async def rescue():
            rescued_calls.append(True)
            return list(_SMALL)

        provider._overflow_rescue = rescue

        out = ""
        async for chunk in provider._stream_chat(list(_BIG)):
            out += chunk
        assert out == "ok"
        assert rescued_calls == [True]
        # Retried with the rescued (post-compact) messages — the tool
        # round was NOT emergency-dropped.
        assert seen[1] == _SMALL

    async def test_successful_rescue_keeps_drop_available(self, monkeypatch):
        # A successful compact rescue must NOT consume the emergency
        # drop: if the rescued (smaller) conversation still overflows,
        # the drop runs on it instead of re-raising.
        provider = OpenAIProvider(api_key="k", model="m")
        seen: list[list] = []
        big = [{"role": "user", "content": "q"}] + list(_BIG)

        async def fake_raw(messages, **kwargs):
            seen.append(list(messages))
            if len(seen) <= 2:
                raise _OverflowErr("context_length_exceeded")
            yield "ok"

        monkeypatch.setattr(provider, "_raw_stream_chat", fake_raw)
        rescued_calls = []

        async def rescue():
            rescued_calls.append(True)
            return list(_MEDIUM)

        provider._overflow_rescue = rescue

        out = ""
        async for chunk in provider._stream_chat(list(big)):
            out += chunk
        assert out == "ok"
        assert rescued_calls == [True]
        assert len(seen) == 3
        assert seen[1] == _MEDIUM
        # Third attempt ran the emergency drop on the rescued list.
        assert all(m.get("role") != "tool" for m in seen[2])

    async def test_non_shrinking_rescue_falls_back_to_drop(self, monkeypatch):
        # An aborted / no-op compact returns a same-size conversation:
        # the rescue must be rejected so the emergency drop still runs
        # instead of burning the single recovery retry on it.
        provider = OpenAIProvider(api_key="k", model="m")
        seen: list[list] = []

        async def fake_raw(messages, **kwargs):
            seen.append(list(messages))
            if any(m.get("role") == "tool" for m in messages):
                raise _OverflowErr("context_length_exceeded")
            yield "ok"

        monkeypatch.setattr(provider, "_raw_stream_chat", fake_raw)

        async def rescue():
            return list(_BIG)

        provider._overflow_rescue = rescue

        out = ""
        async for chunk in provider._stream_chat(list(_BIG)):
            out += chunk
        assert out == "ok"
        assert all(m.get("role") != "tool" for m in seen[-1])

    async def test_rescue_none_falls_back_to_drop(self, monkeypatch):
        provider = OpenAIProvider(api_key="k", model="m")
        seen: list[list] = []

        async def fake_raw(messages, **kwargs):
            seen.append(list(messages))
            if len(seen) == 1:
                raise _OverflowErr("context_length_exceeded")
            yield "ok"

        monkeypatch.setattr(provider, "_raw_stream_chat", fake_raw)

        async def rescue():
            return None

        provider._overflow_rescue = rescue

        out = ""
        async for chunk in provider._stream_chat(list(_BIG)):
            out += chunk
        assert out == "ok"
        # Fell through to the emergency drop — retry has no tool round.
        assert all(m.get("role") != "tool" for m in seen[1])


class TestCompleteOverflowRescue:
    async def test_rescue_consulted_before_dropping_tool_data(self, monkeypatch):
        provider = OpenAIProvider(api_key="k", model="m")
        seen: list[list] = []

        async def fake_raw(messages, **kwargs):
            seen.append(list(messages))
            if len(seen) == 1:
                raise _OverflowErr("context_length_exceeded")
            return ChatResponse(content="ok", finish_reason="stop", usage={}, model="m")

        monkeypatch.setattr(provider, "_raw_complete_chat", fake_raw)

        async def rescue():
            return list(_SMALL)

        provider._overflow_rescue = rescue

        response = await provider._complete_chat(list(_BIG))
        assert response.content == "ok"
        assert seen[1] == _SMALL
