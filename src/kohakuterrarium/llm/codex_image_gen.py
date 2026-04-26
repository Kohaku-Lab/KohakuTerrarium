"""Codex image_generation translation + stream handling.

Kept out of ``codex_provider.py`` to hold the main provider under
the project-wide 600-line file budget. Everything here is pure
helpers — no Codex client state lives in this module.
"""

from typing import Any

from kohakuterrarium.core.native_tool_validation import validate_native_tool_options
from kohakuterrarium.llm.message import ImagePart

# Mapping used when decoding ``image_generation_call.result`` into a
# usable data URL. Default to PNG when the provider didn't tell us.
_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "webp": "image/webp",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
}


def translate_image_gen_tool(tool: Any) -> dict[str, Any] | None:
    """Return the wire-format Codex image-generation tool spec.

    Called by ``CodexOAuthProvider.translate_provider_native_tool``
    when the tool name is ``image_gen``. Merges the tool's declared
    per-instance knobs (``output_format``, ``size``, ``quality``,
    ``action``, ``background``) into the base
    ``{"type":"image_generation"}`` dict. Returns ``None`` if the
    input isn't an image-gen tool — the caller handles that
    by delegating to other translators.
    """
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
        # Minimal fallback if someone subclassed without the helper.
        spec["output_format"] = "png"
    return spec


def build_image_part(item: Any, output_format: str) -> ImagePart | None:
    """Build an ImagePart from a Codex ``image_generation_call`` item.

    Returns ``None`` when the item carries no usable ``result``
    payload (e.g. a partial / in-progress event slipped through).
    The returned part has a ``data:`` URL; the controller will move
    it to disk and rewrite the URL before persisting to conversation.
    """
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
        # Not part of the dataclass — attach dynamically so the
        # controller / session-output layer can surface it without
        # forcing every ImagePart to carry the field.
        setattr(part, "revised_prompt", revised)
    return part
