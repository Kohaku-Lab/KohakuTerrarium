"""Generate Grok Imagine videos as cancellable background tool jobs."""

import uuid
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.tool_output import artifact_served_url
from kohakuterrarium.llm.grok_video import (
    DEFAULT_GROK_VIDEO_MODEL,
    GROK_VIDEO_MODEL_SUGGESTIONS,
    GrokVideoClient,
)
from kohakuterrarium.llm.message import FilePart, TextPart
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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
        return "Generate a video with a selectable Grok Imagine video model"

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "model": {"type": "string"},
                "input_image": {"type": "string"},
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
