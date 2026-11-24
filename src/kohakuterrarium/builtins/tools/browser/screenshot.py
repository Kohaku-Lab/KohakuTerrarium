"""``browser_screenshot``: the page or one element as an image, optionally saved."""

import asyncio
import base64
from pathlib import Path
from typing import Any

from kohakuterrarium.browser.session.types import FULL_PAGE_MAX_HEIGHT
from kohakuterrarium.builtins.tools.browser.common import (
    TAB_ARG,
    ArgumentError,
    BrowserTool,
    bool_arg,
    guarded_path,
    session_metadata,
    tab_arg,
    text_arg,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool.base import ToolContext, ToolResult
from kohakuterrarium.modules.tool.media_policy import METADATA_KEY, MediaPolicy

PATH_ARG = "path"
# Matches the size the request-time image preparation targets.
SCREENSHOT_MAX_BYTES = 512_000
# Context the creature looked at: kept for the model, folded with the tool block.
MEDIA_POLICY = MediaPolicy(persist=True, pinned=False, inline_fallback=True)
TEXT_HINT = (
    "The image follows. Without image input, read the page with browser_snapshot "
    "instead."
)


@register_builtin("browser_screenshot")
class BrowserScreenshotTool(BrowserTool):
    """Return a CSS-pixel screenshot as an image part, with a text line that stands alone."""

    path_arguments = {PATH_ARG: "write"}
    arguments = ("ref", "full_page", PATH_ARG, TAB_ARG)
    media_policy = MEDIA_POLICY
    failure = "no screenshot was taken; the result below says why"

    @property
    def tool_name(self) -> str:
        return "browser_screenshot"

    @property
    def description(self) -> str:
        return (
            "Capture the browser page or one element as an image. Use it for "
            "canvas, charts or layout the snapshot cannot show; optionally save "
            "it to a file."
        )

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        ref = text_arg(args, "ref")
        full_page = bool_arg(args, "full_page")
        if ref is not None and full_page:
            raise ArgumentError("give either ref or full_page, not both")
        raw = text_arg(args, PATH_ARG)
        path = guarded_path(raw, context, PATH_ARG) if raw else None
        return {
            "ref": ref,
            "full_page": full_page,
            "path": path,
            "tab_id": tab_arg(args),
        }

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        result = await session.screenshot(
            ref=call["ref"],
            full_page=call["full_page"],
            max_bytes=SCREENSHOT_MAX_BYTES,
            max_height=FULL_PAGE_MAX_HEIGHT,
            tab_id=call["tab_id"],
        )
        if result.is_error or result.image is None:
            return result
        meta = {
            "tool": self.tool_name,
            "ok": True,
            **result.meta,
            "mime_type": result.mime_type,
            "bytes": len(result.image),
        }
        lines = [result.text]
        path: Path | None = call["path"]
        if path is not None:
            try:
                await asyncio.to_thread(_write, path, result.image)
            except OSError as exc:
                lines.append(f"Not saved to {path} ({type(exc).__name__}).")
            else:
                lines.append(f"Saved to {path} ({result.mime_type}).")
                meta["saved_to"] = str(path)
        lines.append(TEXT_HINT)
        data = base64.b64encode(result.image).decode("ascii")
        image = ImagePart(
            url=f"data:{result.mime_type};base64,{data}",
            detail="auto",
            source_type="browser",
            source_name=f"screenshot {meta.get('tab', '')}".strip(),
        )
        metadata = session_metadata(meta)
        metadata[METADATA_KEY] = MEDIA_POLICY.to_dict()
        return ToolResult(
            output=[TextPart(text="\n".join(lines)), image],
            exit_code=0,
            metadata=metadata,
        )


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
