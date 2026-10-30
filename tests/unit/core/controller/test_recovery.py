"""Session-root isolation across streamed provider execution boundaries."""

import asyncio

import pytest

from kohakuterrarium.core.controller.recovery import chat_with_recovery
from kohakuterrarium.llm.media.artifacts import resolve_message_image_urls
from kohakuterrarium.llm.media.reference import (
    current_media_session_path,
    session_media_scope,
)


class _ScopedProvider:
    def __init__(self, path, *, wait=False):
        self.path = path
        self.wait = wait
        self.entered = asyncio.Event()
        self.closed = False

    def chat(self, messages, **kwargs):
        assert current_media_session_path() == self.path
        assert "media_session_path" not in kwargs
        return self._stream(messages)

    async def _stream(self, messages):
        try:
            self.entered.set()
            if self.wait:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            prepared = await asyncio.to_thread(resolve_message_image_urls, messages)
            yield prepared[0]["content"][0]["image_url"]
            assert current_media_session_path() == self.path
        finally:
            assert current_media_session_path() == self.path
            self.closed = True


async def test_concurrent_provider_steps_resolve_only_their_own_roots_and_restore_consumers(
    tmp_path,
):
    roots = []
    for name in ("left", "right"):
        path = tmp_path / name / "same.kohakutr"
        artifacts = path.with_suffix(".artifacts")
        artifacts.mkdir(parents=True)
        (artifacts / "pic.png").write_bytes(name.encode())
        roots.append(path)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": "/api/sessions/same/artifacts/pic.png",
                }
            ],
        }
    ]
    outer = tmp_path / "outer.kohakutr"

    async def run(path):
        provider = _ScopedProvider(path)
        stream = chat_with_recovery(provider, None, messages, media_session_path=path)
        chunks = []
        async for chunk in stream:
            assert current_media_session_path() == outer
            chunks.append(chunk)
        assert provider.closed
        return chunks

    with session_media_scope(outer):
        left, right = await asyncio.gather(*(run(path) for path in roots))
        assert left == ["data:image/png;base64,bGVmdA=="]
        assert right == ["data:image/png;base64,cmlnaHQ="]
        assert current_media_session_path() == outer
    assert current_media_session_path() is None
    assert messages[0]["content"][0]["image_url"].startswith("/api/")


@pytest.mark.parametrize("cancel", [False, True])
async def test_provider_scope_is_restored_on_early_close_and_cancellation(
    tmp_path, cancel
):
    path = tmp_path / "request.kohakutr"
    provider = _ScopedProvider(path, wait=cancel)
    stream = chat_with_recovery(
        provider,
        None,
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "https://example.invalid/image"}
                ],
            }
        ],
        media_session_path=path,
    )
    if cancel:
        pending = asyncio.create_task(anext(stream))
        await provider.entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        assert await anext(stream) == "https://example.invalid/image"
    assert current_media_session_path() is None
    await stream.aclose()
    assert provider.closed
    assert current_media_session_path() is None
