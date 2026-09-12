"""Promote a local image file onto the Studio canvas."""

import hashlib
import io
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from kohakuterrarium.core.tool_output import artifact_served_url
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.builtins.tools.canvas_preview import build_image_canvas_preview
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
    resolve_tool_path,
)

logger = get_logger(__name__)

MAX_IMAGE_BYTES = 20 * 1024 * 1024

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_PIL_FORMAT_TO_EXT = {
    "PNG": "png",
    "JPEG": "jpg",
    "GIF": "gif",
    "WEBP": "webp",
}


@register_builtin("canvas_image")
class CanvasImageTool(BaseTool):
    """Put a local image file onto the Studio canvas as a product."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "canvas_image"

    @property
    def description(self) -> str:
        return "Put a local image file on the Studio canvas. Not for inspecting a file - use read."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        path = str(args.get("path") or "").strip()
        if not path:
            return ToolResult(error="No path provided")

        file_path = resolve_tool_path(path, context)

        if context and context.path_guard:
            msg = context.path_guard.check(str(file_path))
            if msg:
                return ToolResult(error=msg)

        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        suffix = file_path.suffix.lower()
        if suffix not in _IMAGE_MIME:
            return ToolResult(
                error=(
                    f"Unsupported image format {suffix!r}. "
                    f"Supported: {', '.join(sorted(_IMAGE_MIME))}."
                )
            )

        file_size = file_path.stat().st_size
        if file_size > MAX_IMAGE_BYTES:
            return ToolResult(
                error=(
                    f"Image too large ({file_size // 1024}KB). "
                    f"Max: {MAX_IMAGE_BYTES // (1024 * 1024)}MB."
                )
            )

        try:
            data = file_path.read_bytes()
        except OSError as e:
            return ToolResult(error=f"Failed to read image: {e}")

        verified = _verify_image(data)
        if verified not in _PIL_FORMAT_TO_EXT:
            return ToolResult(
                error=(
                    f"File {path} is not a valid image, or its format "
                    "is unsupported (need PNG / JPEG / WEBP / GIF)."
                )
            )

        lang = _PIL_FORMAT_TO_EXT[verified]
        url = _display_url(file_path, data, context)
        resolved = str(file_path.resolve())

        logger.info(
            "Image promoted to canvas",
            file_path=resolved,
            size_kb=len(data) // 1024,
            lang=lang,
        )

        return ToolResult(
            output=[
                TextPart(text=f"Canvas: {path} ({len(data) // 1024}KB, {lang})"),
                ImagePart(
                    url=url,
                    detail="auto",
                    source_type="file",
                    source_name=file_path.name,
                ),
            ],
            exit_code=0,
            metadata={
                "canvas_preview": build_image_canvas_preview(
                    resolved,
                    url,
                    lang=lang,
                    nbytes=len(data),
                ),
            },
        )


def _display_url(file_path: Path, data: bytes, context: ToolContext | None) -> str:
    store = getattr(getattr(context, "agent", None), "session_store", None)
    if store is not None and hasattr(store, "write_artifact"):
        digest = hashlib.sha256(data).hexdigest()
        rel = f"canvas_images/{digest}/{_safe_filename(file_path.name)}"
        try:
            disk_path = store.write_artifact(rel, data)
            return artifact_served_url(store, rel, disk_path)
        except Exception as e:
            logger.warning(
                "Failed to copy image into session artifacts",
                error=str(e),
            )
    return file_path.resolve().as_uri()


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in name.strip())
    return cleaned or "image.png"


def _verify_image(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.load()
        return fmt
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as e:
        logger.debug("Image verification failed", error=str(e))
        return None
