"""Generate and edit images through the Codex subscription's Images endpoints."""

import asyncio
from typing import Any, ClassVar

from kohakuterrarium.builtins.tools.media.image_inputs import (
    ImageInputError,
    file_data_url,
    recent_image_urls,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.codex.image.client import (
    GIF_FRAME_MS,
    GIF_FRAMES,
    MAX_EDIT_IMAGES,
    CodexImageClient,
    CodexImageError,
)
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ToolContext,
    ToolResult,
    resolve_tool_path,
)

_GUIDANCE = (
    "image_gen needs a minute or more per image. Generate directly, without asking "
    "to confirm. Always edit existing images with image_gen, never with python or "
    "bash. For an edit, list the target files in referenced_image_paths; use "
    "num_last_images_to_include only for images that have no file. If you have not "
    "seen a local image yet, read it first."
)


@register_builtin("image_gen")
class ImageGenTool(BaseTool):
    """Text-to-image and image editing with the model Codex uses."""

    needs_context = True
    path_arguments: ClassVar[dict[str, str]] = {"referenced_image_paths": "read"}

    def __init__(self, config=None, *, client: CodexImageClient | None = None):
        super().__init__(config=config)
        self._client = client
        self.gif = False
        self.refresh_runtime_options(dict(self.config.extra))

    @property
    def tool_name(self) -> str:
        return "image_gen"

    @property
    def description(self) -> str:
        return (
            "Generate or edit a raster image. Use it whenever a picture, mockup or "
            "photo edit helps. Not for diagrams from code - use bash to render the "
            "source."
        )

    def prompt_contribution(self) -> str | None:
        return _GUIDANCE

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        return {
            "gif": {
                "type": "bool",
                "default": False,
                "doc": "Offer animated GIF output (needs a backend with GIF enabled).",
            }
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.gif = bool(options.get("gif", False))

    def get_parameters_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "prompt": {
                "type": "string",
                "description": "Image instructions: a scene to create, or the changes to make",
            },
            "transparent_background": {
                "type": "boolean",
                "description": "Transparent background, for cutouts and stickers (default false)",
            },
            "referenced_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_EDIT_IMAGES,
                "description": "Local images to edit",
            },
            "num_last_images_to_include": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_EDIT_IMAGES,
                "description": "Edit the most recent conversation images instead of files",
            },
        }
        if self.gif:
            properties["gif"] = {
                "type": "object",
                "description": "Make a looping GIF; {} keeps the defaults",
                "properties": {
                    "frame_count": {
                        "type": "integer",
                        "minimum": GIF_FRAMES[0],
                        "maximum": GIF_FRAMES[1],
                    },
                    "frame_duration_ms": {
                        "type": "integer",
                        "minimum": GIF_FRAME_MS[0],
                        "maximum": GIF_FRAME_MS[1],
                    },
                },
            }
        return {"type": "object", "properties": properties, "required": ["prompt"]}

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None, **kwargs: Any
    ) -> ToolResult:
        gif = args.get("gif")
        if gif is not None and not self.gif:
            return ToolResult(error="GIF output is not enabled for image_gen")
        options = {
            "transparent_background": bool(args.get("transparent_background", False)),
            "gif": gif,
        }
        prompt = str(args.get("prompt") or "")
        try:
            images = await self._input_images(args, context)
            client = self._client or _client_for(context)
            if images:
                parts = await client.edit(prompt, images, **options)
            else:
                parts = await client.generate(prompt, **options)
        except (
            ImageInputError,
            ValueError,
            CodexImageError,
            LLMNotConfiguredError,
        ) as exc:
            return ToolResult(error=str(exc))
        return ToolResult(
            output=parts,
            metadata={
                "image_count": len(parts),
                "edited_images": len(images),
                "_image_artifact_subdir": "generated_images",
            },
        )

    async def _input_images(
        self, args: dict[str, Any], context: ToolContext | None
    ) -> list[str]:
        paths = args.get("referenced_image_paths") or []
        last = args.get("num_last_images_to_include")
        if paths and last is not None:
            raise ImageInputError(
                "provide only one of referenced_image_paths or num_last_images_to_include"
            )
        if paths:
            if len(paths) > MAX_EDIT_IMAGES:
                raise ImageInputError(
                    f"referenced_image_paths takes at most {MAX_EDIT_IMAGES} paths"
                )
            resolved = [resolve_tool_path(str(path), context) for path in paths]
            return [await asyncio.to_thread(file_data_url, path) for path in resolved]
        if last is not None:
            if isinstance(last, bool) or not isinstance(last, int):
                raise ImageInputError(
                    "num_last_images_to_include must be a whole number"
                )
            if not 1 <= last <= MAX_EDIT_IMAGES:
                raise ImageInputError(
                    f"num_last_images_to_include must be from 1 to {MAX_EDIT_IMAGES}"
                )
            conversation = getattr(
                getattr(getattr(context, "agent", None), "controller", None),
                "conversation",
                None,
            )
            if conversation is None:
                raise ImageInputError("the conversation is not available to this tool")
            return recent_image_urls(conversation.get_messages(), last)
        return []


def _client_for(context: ToolContext | None) -> CodexImageClient:
    """A client for the agent's own Codex backend (custom URL, API key), else the login."""
    llm = getattr(getattr(context, "agent", None), "llm", None)
    endpoint = getattr(llm, "image_endpoint", None)
    if isinstance(endpoint, tuple):
        base_url, api_key = endpoint
        return CodexImageClient(base_url=base_url, api_key=api_key)
    return CodexImageClient()
