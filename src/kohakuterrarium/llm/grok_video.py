"""Asynchronous xAI video generation and bounded result download."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from kohakuterrarium.llm.grok_media import GrokMediaClient, GrokMediaError
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_GROK_VIDEO_MODEL = "grok-imagine-video-1.5"
GROK_VIDEO_MODEL_SUGGESTIONS = (
    "grok-imagine-video-1.5",
    "grok-imagine-video",
)
MAX_VIDEO_BYTES = 128 * 1024 * 1024
DEFAULT_POLL_TIMEOUT = 10 * 60.0
VIDEO_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
VIDEO_RESOLUTIONS = {"480p", "720p", "1080p"}


@dataclass(frozen=True)
class GrokVideoResult:
    """Materialized video data and provider metadata."""

    content: bytes
    mime: str
    model: str
    request_id: str
    duration: float | None = None


class GrokVideoClient:
    """Submit, poll, and download a video from documented xAI endpoints."""

    def __init__(
        self,
        media: GrokMediaClient | None = None,
        *,
        poll_interval: float = 5.0,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.media = media or GrokMediaClient(timeout=600.0)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.sleep = sleep
        self.clock = clock

    async def generate(self, args: dict[str, Any]) -> GrokVideoResult:
        payload = _video_payload(args)
        async with self.media.request_session() as media:
            return await self._generate(payload, media)

    async def _generate(
        self, payload: dict[str, Any], media: GrokMediaClient
    ) -> GrokVideoResult:
        submitted = await media.request_json(
            "POST",
            "videos/generations",
            payload=payload,
            operation="video generation",
        )
        request_id = submitted.data.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise GrokMediaError(200, "video generation")

        logger.info(
            "Grok video generation submitted",
            request_id=request_id,
            model=payload["model"],
            duration=payload.get("duration"),
            aspect_ratio=payload.get("aspect_ratio"),
            resolution=payload.get("resolution"),
        )

        started_at = self.clock()
        deadline = started_at + self.poll_timeout
        last_status: str | None = None
        while True:
            status_response = await media.request_json(
                "GET",
                f"videos/{request_id}",
                token=submitted.token,
                operation="video polling",
            )
            status = status_response.data.get("status")
            if status != last_status:
                logger.debug(
                    "Grok video generation status",
                    request_id=request_id,
                    status=status,
                )
                last_status = status if isinstance(status, str) else None
            if status == "done":
                try:
                    result = await self._download_result(
                        status_response.data,
                        request_id,
                        str(payload["model"]),
                        media,
                    )
                except GrokMediaError:
                    logger.warning(
                        "Grok video result download failed",
                        request_id=request_id,
                    )
                    raise
                logger.info(
                    "Grok video generation completed",
                    request_id=request_id,
                    model=result.model,
                    duration=result.duration,
                    mime=result.mime,
                    bytes=len(result.content),
                    elapsed_seconds=round(self.clock() - started_at, 3),
                )
                return result
            if status in {"failed", "expired"}:
                logger.warning(
                    "Grok video generation failed",
                    request_id=request_id,
                    status=status,
                )
                raise GrokMediaError(
                    200,
                    f"video generation ({status})",
                    request_id=request_id,
                )
            if status not in {"pending", "processing", "queued"}:
                logger.warning(
                    "Grok video generation returned an unknown status",
                    request_id=request_id,
                    status=status,
                )
                raise GrokMediaError(200, "video polling", request_id=request_id)
            if self.clock() >= deadline:
                logger.warning(
                    "Grok video generation timed out",
                    request_id=request_id,
                    timeout_seconds=self.poll_timeout,
                )
                raise GrokMediaError(
                    None,
                    "video polling timeout",
                    request_id=request_id,
                )
            await self.sleep(self.poll_interval)

    async def _download_result(
        self,
        data: dict[str, Any],
        request_id: str,
        model: str,
        media: GrokMediaClient,
    ) -> GrokVideoResult:
        video = data.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("url"), str):
            raise GrokMediaError(200, "video result")
        content, mime = await media.download_bytes(
            video["url"], max_bytes=MAX_VIDEO_BYTES
        )
        if not content or mime not in {"video/mp4", "application/octet-stream"}:
            raise GrokMediaError(200, "video result")
        duration = video.get("duration")
        return GrokVideoResult(
            content=content,
            mime="video/mp4" if mime == "application/octet-stream" else mime,
            model=str(data.get("model") or model),
            request_id=request_id,
            duration=float(duration) if isinstance(duration, (int, float)) else None,
        )


def _video_payload(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    payload: dict[str, Any] = {
        "model": str(args.get("model") or DEFAULT_GROK_VIDEO_MODEL),
        "prompt": prompt,
    }
    image_url = str(args.get("input_image") or "").strip()
    if image_url:
        payload["image"] = {"url": image_url}
    duration = args.get("duration")
    if duration not in (None, ""):
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not 1 <= duration <= 15
        ):
            raise ValueError("duration must be an integer between 1 and 15")
        payload["duration"] = duration
    aspect_ratio = args.get("aspect_ratio")
    if aspect_ratio not in (None, ""):
        if aspect_ratio not in VIDEO_ASPECT_RATIOS:
            raise ValueError("unsupported video aspect_ratio")
        payload["aspect_ratio"] = aspect_ratio
    resolution = args.get("resolution")
    if resolution not in (None, ""):
        if resolution not in VIDEO_RESOLUTIONS:
            raise ValueError("unsupported video resolution")
        payload["resolution"] = resolution
    return payload


__all__ = [
    "DEFAULT_GROK_VIDEO_MODEL",
    "DEFAULT_POLL_TIMEOUT",
    "GROK_VIDEO_MODEL_SUGGESTIONS",
    "MAX_VIDEO_BYTES",
    "VIDEO_ASPECT_RATIOS",
    "VIDEO_RESOLUTIONS",
    "GrokVideoClient",
    "GrokVideoResult",
]
