"""Generate and edit Grok Imagine images through the official Images API."""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.llm.grok_image_gen import (
    DEFAULT_GROK_IMAGE_MODEL,
    GROK_IMAGE_MODEL_SUGGESTIONS,
    GrokImageClient,
)
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


@register_builtin("grok_image_gen")
class GrokImageGenTool(BaseTool):
    """Use a selectable xAI image model without treating it as a chat model."""

    def __init__(self, config=None, *, client: GrokImageClient | None = None):
        super().__init__(config=config)
        self.client = client or GrokImageClient()
        self.model = DEFAULT_GROK_IMAGE_MODEL
        self.refresh_runtime_options(dict(self.config.extra))

    @property
    def tool_name(self) -> str:
        return "grok_image_gen"

    @property
    def description(self) -> str:
        return "Generate or edit an image with xAI's dedicated image endpoint. Not for other providers - use image_gen."

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image instructions"},
                "model": {
                    "type": "string",
                    "description": "xAI image model, not a language model",
                },
                "action": {
                    "type": "string",
                    "enum": ["generate", "edit"],
                    "default": "generate",
                },
                "image_url": {
                    "type": "string",
                    "description": "Required source image URL for edit",
                },
                "n": {"type": "integer", "minimum": 1, "maximum": 10},
                "aspect_ratio": {
                    "type": "string",
                    "enum": [
                        "auto",
                        "1:1",
                        "16:9",
                        "9:16",
                        "4:3",
                        "3:4",
                        "3:2",
                        "2:3",
                        "2:1",
                        "1:2",
                        "19.5:9",
                        "9:19.5",
                        "20:9",
                        "9:20",
                    ],
                },
                "resolution": {"type": "string", "enum": ["1k", "2k"]},
                "quality": {"type": "string", "enum": ["low", "medium"]},
            },
            "required": ["prompt"],
        }

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "model": {
                "type": "string",
                "default": DEFAULT_GROK_IMAGE_MODEL,
                "suggestions": list(GROK_IMAGE_MODEL_SUGGESTIONS),
                "doc": "Default xAI image-generation model.",
            }
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.model = str(options.get("model") or DEFAULT_GROK_IMAGE_MODEL)

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        effective = dict(args)
        effective.setdefault("model", self.model)
        action = str(effective.pop("action", "generate"))
        parts = (
            await self.client.edit(effective)
            if action == "edit"
            else await self.client.generate(effective)
        )
        return ToolResult(
            output=parts,
            metadata={
                "model": effective["model"],
                "image_count": len(parts),
                "_image_artifact_subdir": "generated_images",
            },
        )
