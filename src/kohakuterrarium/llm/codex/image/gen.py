"""Decode hosted ``image_generation_call`` results streamed by Codex."""

from typing import Any

from kohakuterrarium.llm.message import ImagePart

# Unknown output formats use PNG so the resulting data URL remains valid.
_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "webp": "image/webp",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
}


def build_image_part(item: Any, output_format: str = "png") -> ImagePart | None:
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
