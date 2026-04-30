"""Unit coverage for the Codex image-gen translation + stream parsing.

Covers the seam the PoC confirmed live against Codex:

1. ``translate_provider_native_tool`` emits the ``image_generation``
   wire-format spec and threads through the tool's knobs.
2. ``build_image_part`` wraps the base64 ``result`` into an
   ImagePart with the right data URL (preserves PoC behaviour).
3. ``_handle_image_generation_call`` accumulates into
   ``last_assistant_content_parts`` so the controller can pick it up.

We don't hit the network here — the Responses SDK stream is faked
with simple duck-typed namespace objects matching the fields we
observed in ``poc/codex_image_gen/FINDINGS.md``.
"""

import asyncio
from types import SimpleNamespace

from kohakuterrarium.builtins.tools import ImageGenTool
from kohakuterrarium.llm.codex_image_gen import (
    build_image_part,
    translate_image_gen_tool,
)
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.modules.tool.base import ToolConfig

# --- translate_image_gen_tool -----------------------------------------


def test_translate_image_gen_tool_defaults():
    tool = ImageGenTool()
    spec = translate_image_gen_tool(tool)
    assert spec == {"type": "image_generation", "output_format": "png"}


def test_translate_image_gen_tool_with_knobs():
    tool = ImageGenTool(
        config=ToolConfig(
            extra={"size": "1024x1024", "quality": "high", "action": "edit"}
        )
    )
    spec = translate_image_gen_tool(tool)
    assert spec["type"] == "image_generation"
    assert spec["output_format"] == "png"
    assert spec["size"] == "1024x1024"
    assert spec["quality"] == "high"
    assert spec["action"] == "edit"


def test_translate_ignores_unknown_tools():
    unrelated = SimpleNamespace(tool_name="bash")
    assert translate_image_gen_tool(unrelated) is None


# --- build_image_part -------------------------------------------------


def test_build_image_part_wraps_base64_as_data_url():
    item = SimpleNamespace(
        result="QUJD",  # "ABC" base64
        id="ig_abc123",
        revised_prompt="a cat eating ramen",
    )
    part = build_image_part(item, output_format="png")
    assert isinstance(part, ImagePart)
    assert part.url == "data:image/png;base64,QUJD"
    assert part.source_type == "image_gen"
    assert part.source_name == "ig_abc123"
    assert getattr(part, "revised_prompt") == "a cat eating ramen"


def test_build_image_part_handles_jpeg():
    item = SimpleNamespace(result="XYZ", id="ig_2")
    part = build_image_part(item, output_format="jpeg")
    assert part.url.startswith("data:image/jpeg;base64,")


def test_build_image_part_returns_none_when_empty_result():
    item = SimpleNamespace(result=None, id="ig_3")
    assert build_image_part(item, "png") is None


# --- Provider stream integration -------------------------------------


class _FakeStream:
    """Minimal async iterator yielding a scripted sequence of events.

    Matches the real SDK's event shape as observed live in the PoC
    (see poc/codex_image_gen/FINDINGS.md). We only model the fields
    the provider reads.
    """

    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def _scripted_events():
    image_call = SimpleNamespace(
        type="image_generation_call",
        id="ig_xyz",
        status="generating",  # NB: matches live wire; NOT "completed"
        result="QUJD",
        revised_prompt="A cute otter",
    )
    return [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="Here's your otter. "),
        SimpleNamespace(type="response.output_item.done", item=image_call),
        SimpleNamespace(type="response.completed", response=None),
    ]


class _FakeResponses:
    def __init__(self, stream):
        self._stream = stream

    async def create(self, **kwargs):
        _FakeResponses.last_call = kwargs  # expose to test
        return self._stream


class _FakeClient:
    def __init__(self, stream):
        self.responses = _FakeResponses(stream)

    async def close(self):
        pass


async def _consume(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


def test_provider_collects_image_from_stream(monkeypatch):
    provider = CodexOAuthProvider(model="gpt-5.4")
    provider._tokens = SimpleNamespace(access_token="dummy", is_expired=lambda: False)
    provider._client = _FakeClient(_FakeStream(_scripted_events()))

    # Bypass token refresh + re-auth — the fake client is already set.
    async def _noop():
        return None

    provider._ensure_valid_token = _noop  # type: ignore[assignment]

    image_tool = ImageGenTool()
    text_chunks = asyncio.run(
        _consume(
            provider.chat(
                [{"role": "user", "content": "draw me an otter"}],
                stream=True,
                provider_native_tools=[image_tool],
            )
        )
    )
    assert "".join(text_chunks) == "Here's your otter. "

    parts = provider.last_assistant_content_parts
    assert parts is not None
    assert len(parts) == 1
    assert isinstance(parts[0], ImagePart)
    assert parts[0].url == "data:image/png;base64,QUJD"
    assert parts[0].source_name == "ig_xyz"

    # Provider must have injected the image_generation tool spec.
    tools_sent = _FakeResponses.last_call["tools"]
    assert {"type": "image_generation", "output_format": "png"} in tools_sent


def test_provider_last_parts_reset_between_turns(monkeypatch):
    provider = CodexOAuthProvider(model="gpt-5.4")
    provider._tokens = SimpleNamespace(access_token="dummy", is_expired=lambda: False)

    async def _noop():
        return None

    provider._ensure_valid_token = _noop  # type: ignore[assignment]

    # First turn emits an image.
    provider._client = _FakeClient(_FakeStream(_scripted_events()))
    asyncio.run(
        _consume(
            provider.chat(
                [{"role": "user", "content": "draw me an otter"}],
                stream=True,
                provider_native_tools=[ImageGenTool()],
            )
        )
    )
    assert provider.last_assistant_content_parts is not None

    # Second turn — text only, no image item.
    provider._client = _FakeClient(
        _FakeStream(
            [
                SimpleNamespace(type="response.output_text.delta", delta="just text"),
                SimpleNamespace(type="response.completed", response=None),
            ]
        )
    )
    asyncio.run(
        _consume(provider.chat([{"role": "user", "content": "anything?"}], stream=True))
    )
    # last_assistant_content_parts should now be None (empty list → None
    # per the property contract) so the controller falls back to text.
    assert provider.last_assistant_content_parts is None


def test_provider_strips_surrogates_from_stream(monkeypatch):
    provider = CodexOAuthProvider(model="gpt-5.4")
    provider._tokens = SimpleNamespace(access_token="dummy", is_expired=lambda: False)
    provider._client = _FakeClient(
        _FakeStream(
            [
                SimpleNamespace(type="response.output_text.delta", delta="ok\udcaf!"),
                SimpleNamespace(type="response.completed", response=None),
            ]
        )
    )

    async def _noop():
        return None

    provider._ensure_valid_token = _noop  # type: ignore[assignment]

    chunks = asyncio.run(
        _consume(provider.chat([{"role": "user", "content": "x"}], stream=True))
    )

    assert chunks == ["ok!"]
