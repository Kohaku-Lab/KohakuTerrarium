"""Codex image_generation translation + stream handling.

Translate Codex image-generation tools and decode their streamed results.
"""

from typing import Any

from kohakuterrarium.core.native_tool_validation import validate_native_tool_options
from kohakuterrarium.llm.message import ImagePart

# Unknown output formats use PNG so the resulting data URL remains valid.
_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "webp": "image/webp",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
}


def translate_image_gen_tool(tool: Any) -> dict[str, Any] | None:
    """Translate an image-generation tool into a validated Codex wire schema."""
    name = getattr(tool, "tool_name", None) or getattr(tool, "name", None)
    if name != "image_gen":
        return None
    spec: dict[str, Any] = {"type": "image_generation"}
    if hasattr(tool, "provider_native_options"):
        options = tool.provider_native_options()
        schema_fn = getattr(type(tool), "provider_native_option_schema", None)
        schema = schema_fn() if callable(schema_fn) else {}
        options = validate_native_tool_options("image_gen", options, schema)
        spec.update(options)
    else:
        # Subclasses without option helpers still need a deterministic media type.
        spec["output_format"] = "png"
    return spec


def build_image_part(item: Any, output_format: str) -> ImagePart | None:
    """Decode a completed image result into an inline image part."""
    result = getattr(item, "result", None)
    if not result:
        return None
    ext = (output_format or "png").lower()
    mime = _MIME_BY_EXT.get(ext, "image/png")
    part = ImagePart(
        url=f"data:{mime};base64,{result}",
        detail="auto",
        source_type="image_gen",
        source_name=getattr(item, "id", None),
    )
    revised = getattr(item, "revised_prompt", None)
    if revised:
        # Revised prompts are image-generation metadata, not a field shared by all images.
        setattr(part, "revised_prompt", revised)
    return part
