"""Generate Grok Imagine videos as cancellable background tool jobs."""

import base64
import binascii
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kohakuterrarium.builtins.tools.read import MAX_IMAGE_BYTES, ReadTool
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.tool_output import artifact_served_url
from kohakuterrarium.llm.artifact_resolve import resolve_artifact_url
from kohakuterrarium.llm.grok_video import (
    DEFAULT_GROK_VIDEO_MODEL,
    GROK_VIDEO_MODEL_SUGGESTIONS,
    GrokVideoClient,
)
from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DATA_IMAGE_RE = re.compile(
    r"^data:(?P<mime>image/(?:png|jpeg|webp|gif));base64,(?P<data>.+)$", re.S
)


@register_builtin("video_gen")
class VideoGenTool(BaseTool):
    """Generate a video through xAI's asynchronous video API."""

    needs_context = True

    def __init__(self, config=None, *, client: GrokVideoClient | None = None):
        super().__init__(config=config)
        self.client = client or GrokVideoClient()
        self.model = DEFAULT_GROK_VIDEO_MODEL
        self.refresh_runtime_options(dict(self.config.extra))

    @property
    def tool_name(self) -> str:
        return "video_gen"

    @property
    def description(self) -> str:
        return "Generate a video from a prompt. Not for still images - use image_gen."

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "model": {"type": "string"},
                "input_image": {
                    "type": "string",
                    "description": (
                        "Public image URL, readable local path, KT artifact URL, "
                        "or 'latest' for the latest attached image. Never encode "
                        "the image yourself; the tool handles it internally"
                    ),
                },
                "duration": {"type": "integer", "minimum": 1, "maximum": 15},
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
                },
                "resolution": {
                    "type": "string",
                    "enum": ["480p", "720p", "1080p"],
                },
            },
            "required": ["prompt"],
        }

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "model": {
                "type": "string",
                "default": DEFAULT_GROK_VIDEO_MODEL,
                "suggestions": list(GROK_VIDEO_MODEL_SUGGESTIONS),
                "doc": "Default xAI video-generation model.",
            }
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.model = str(options.get("model") or DEFAULT_GROK_VIDEO_MODEL)

    async def _execute(
        self, args: dict[str, Any], *, context: ToolContext | None = None, **kwargs: Any
    ) -> ToolResult:
        if context is None:
            return ToolResult(error="video_gen requires an agent execution context")
        effective = dict(args)
        effective.setdefault("model", self.model)
        if effective.get("input_image"):
            effective["input_image"] = await _resolve_input_image(
                str(effective["input_image"]), context
            )
        result = await self.client.generate(effective)
        filename = f"generated_videos/grok_{uuid.uuid4().hex}.mp4"
        served_path, disk_path = _write_video_artifact(
            context, filename, result.content
        )
        logger.info(
            "Video artifact persisted",
            artifact_path=disk_path,
            artifact_url=served_path,
        )
        return ToolResult(
            output=[
                TextPart(text=f"Video saved to: {disk_path}"),
                FilePart(
                    path=served_path,
                    name=Path(filename).name,
                    mime=result.mime,
                ),
            ],
            metadata={
                "model": result.model,
                "request_id": result.request_id,
                "duration": result.duration,
                "session_metadata": {
                    "artifacts": [
                        {
                            "kind": "video",
                            "relative_path": filename,
                            "url": served_path,
                        }
                    ]
                },
            },
        )


def _write_video_artifact(
    context: ToolContext, filename: str, content: bytes
) -> tuple[str, str]:
    store = getattr(context.agent, "session_store", None)
    if store is not None and hasattr(store, "write_artifact"):
        disk_path = store.write_artifact(filename, content)
        return artifact_served_url(store, filename, disk_path), str(disk_path)

    target = context.resolve_path(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return str(target), str(target)


async def _resolve_input_image(reference: str, context: ToolContext) -> str:
    reference = reference.strip()
    if reference.lower() == "latest":
        attached = _latest_attached_image(context)
        if attached is None:
            raise ValueError("No attached image is available for input_image='latest'")
        return attached

    inlined = _inline_local_reference(reference)
    if inlined is not None:
        return inlined
    if _is_local_reference(reference):
        raise ValueError("Could not resolve the local media reference for input_image")

    parsed = urlparse(reference)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return reference

    path = context.resolve_path(reference)
    if _is_temporary_path(path) and not path.is_file():
        attached = _latest_attached_image(context)
        if attached is not None:
            return attached

    result = await ReadTool().execute({"path": reference}, context=context)
    if result.error:
        raise ValueError(result.error)
    if isinstance(result.output, list):
        image = next(
            (part for part in result.output if isinstance(part, ImagePart)), None
        )
        inlined = _inline_local_reference(image.url) if image is not None else None
        if inlined is not None:
            return inlined
    raise ValueError("input_image is not a supported image")


def _is_local_reference(url: str) -> bool:
    """Return whether ``url`` names media KT itself can read from disk."""
    return url.startswith("/api/sessions/") or url.startswith("file://")


def _inline_local_reference(url: str) -> str | None:
    """Return the validated data URL behind a stored media reference, or ``None``.

    Accepts an inline ``data:`` URL as-is, and reads session artifacts and
    ``file://`` references the way the provider boundary does.
    """
    if url.startswith("data:"):
        return _validated_data_image(url)
    if not _is_local_reference(url):
        return None
    resolved = resolve_artifact_url(url)
    if resolved == url:
        return None
    return _validated_data_image(resolved)


def _latest_attached_image(context: ToolContext) -> str | None:
    messages = getattr(getattr(context, "agent", None), "conversation_history", None)
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if message.get("role") != "user" or not isinstance(
            message.get("content"), list
        ):
            continue
        for part in reversed(message["content"]):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                image = part.get("image_url")
                url = image.get("url") if isinstance(image, dict) else None
                if isinstance(url, str) and url:
                    inlined = _inline_local_reference(url)
                    if inlined is not None:
                        return inlined
                    parsed = urlparse(url)
                    if parsed.scheme in {"http", "https"} and parsed.hostname:
                        return url
            if part.get("type") != "file":
                continue
            file_data = part.get("file")
            if not isinstance(file_data, dict):
                continue
            mime = file_data.get("mime")
            encoded = file_data.get("data_base64")
            if (
                isinstance(mime, str)
                and mime.startswith("image/")
                and isinstance(encoded, str)
                and encoded
            ):
                return _validated_data_image(f"data:{mime};base64,{encoded}")
    return None


def _validated_data_image(reference: str) -> str:
    match = _DATA_IMAGE_RE.fullmatch(reference)
    if match is None:
        raise ValueError(
            "input_image data URI must contain a base64 PNG, JPEG, WEBP, or GIF"
        )
    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("input_image contains invalid base64 data") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"input_image exceeds KT's {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit"
        )
    return reference


def _is_temporary_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        return False
    return True
