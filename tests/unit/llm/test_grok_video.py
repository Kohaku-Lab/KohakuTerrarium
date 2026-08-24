"""Tests for xAI video submission, polling, and download."""

from contextlib import asynccontextmanager

import pytest

from kohakuterrarium.llm.grok_auth import GrokToken
from kohakuterrarium.llm.grok_media import GrokMediaError, GrokMediaResponse
from kohakuterrarium.llm.grok_video import GrokVideoClient


class _Media:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []
        self.token = GrokToken(access_token="secret", source="test")

    @asynccontextmanager
    async def request_session(self):
        yield self

    async def request_json(self, method, path, *, payload=None, token=None, operation):
        self.calls.append((method, path, payload, token, operation))
        if path == "videos/generations":
            return GrokMediaResponse(data={"request_id": "req-1"}, token=self.token)
        return GrokMediaResponse(data=self.statuses.pop(0), token=self.token)

    async def download_bytes(self, url, *, max_bytes):
        assert url == "https://vidgen.x.ai/result.mp4"
        return b"mp4", "video/mp4"


class _LogCapture:
    def __init__(self):
        self.calls = []

    def info(self, message, **fields):
        self.calls.append(("info", message, fields))

    def debug(self, message, **fields):
        self.calls.append(("debug", message, fields))

    def warning(self, message, **fields):
        self.calls.append(("warning", message, fields))


class TestGrokVideoClient:
    @pytest.mark.asyncio
    async def test_submit_poll_and_download(self):
        media = _Media(
            [
                {"status": "pending"},
                {
                    "status": "done",
                    "model": "grok-imagine-video-1.5",
                    "video": {
                        "url": "https://vidgen.x.ai/result.mp4",
                        "duration": 8,
                    },
                },
            ]
        )
        sleeps = []

        async def sleep(delay):
            sleeps.append(delay)

        client = GrokVideoClient(media=media, poll_interval=0.25, sleep=sleep)

        result = await client.generate(
            {
                "prompt": "cat",
                "model": "grok-imagine-video-1.5",
                "duration": 8,
                "resolution": "720p",
            }
        )

        assert result.content == b"mp4"
        assert result.duration == 8
        assert sleeps == [0.25]
        submit = media.calls[0]
        assert submit[1] == "videos/generations"
        assert submit[2]["resolution"] == "720p"
        assert media.calls[1][3] is media.token

    @pytest.mark.asyncio
    async def test_logs_lifecycle_without_sensitive_request_data(self, monkeypatch):
        from kohakuterrarium.llm import grok_video

        media = _Media(
            [
                {"status": "pending"},
                {
                    "status": "done",
                    "video": {"url": "https://vidgen.x.ai/result.mp4"},
                },
            ]
        )
        logs = _LogCapture()
        monkeypatch.setattr(grok_video, "logger", logs)

        async def sleep(_delay):
            return None

        await GrokVideoClient(media=media, sleep=sleep).generate(
            {"prompt": "private prompt", "resolution": "720p"}
        )

        messages = [message for _level, message, _fields in logs.calls]
        assert "Grok video generation submitted" in messages
        assert messages.count("Grok video generation status") == 2
        assert "Grok video generation completed" in messages
        serialized = repr(logs.calls)
        assert "private prompt" not in serialized
        assert "secret" not in serialized
        assert "https://vidgen.x.ai" not in serialized

    @pytest.mark.asyncio
    async def test_failed_status_is_terminal(self):
        media = _Media([{"status": "failed"}])
        client = GrokVideoClient(media=media)

        with pytest.raises(GrokMediaError, match="failed"):
            await client.generate({"prompt": "cat"})

    @pytest.mark.asyncio
    async def test_image_to_video_uses_official_image_shape(self):
        media = _Media(
            [
                {
                    "status": "done",
                    "video": {"url": "https://vidgen.x.ai/result.mp4"},
                }
            ]
        )
        client = GrokVideoClient(media=media)

        await client.generate(
            {"prompt": "move", "input_image": "data:image/png;base64,abc"}
        )

        assert media.calls[0][2]["image"] == {"url": "data:image/png;base64,abc"}

    @pytest.mark.asyncio
    async def test_polling_has_a_hard_timeout(self):
        media = _Media([{"status": "pending"}])
        times = iter([0.0, 11.0])

        client = GrokVideoClient(
            media=media,
            poll_timeout=10.0,
            clock=lambda: next(times),
        )

        with pytest.raises(GrokMediaError, match="polling timeout"):
            await client.generate({"prompt": "cat"})

    @pytest.mark.asyncio
    async def test_invalid_duration_is_rejected_before_submission(self):
        media = _Media([])

        with pytest.raises(ValueError, match="between 1 and 15"):
            await GrokVideoClient(media=media).generate(
                {"prompt": "cat", "duration": 16}
            )

        assert media.calls == []
