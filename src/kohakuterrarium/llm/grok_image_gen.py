"""xAI Images API adapter used by the Grok image-generation tool."""

from typing import Any

from kohakuterrarium.llm.grok_media import GrokMediaClient
from kohakuterrarium.llm.message import ImagePart

DEFAULT_GROK_IMAGE_MODEL = "grok-imagine-image-2.0"
GROK_IMAGE_MODEL_SUGGESTIONS = (
    "grok-imagine-image-2.0",
    "grok-imagine-image-quality",
    "grok-imagine-image",
)


class GrokImageClient:
    """Generate or edit images through xAI's documented image endpoints."""

    def __init__(self, media: GrokMediaClient | None = None) -> None:
        self.media = media or GrokMediaClient()

    async def generate(self, args: dict[str, Any]) -> list[ImagePart]:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        payload = _generation_payload(args, prompt)
        response = await self.media.request_json(
            "POST",
            "images/generations",
            payload=payload,
            operation="image generation",
        )
        return _image_parts(response.data, payload["model"])

    async def edit(self, args: dict[str, Any]) -> list[ImagePart]:
        prompt = str(args.get("prompt") or "").strip()
        image_url = str(args.get("image_url") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        if not image_url:
            raise ValueError("image_url is required for editing")
        payload = _generation_payload(args, prompt)
        payload["image"] = {"url": image_url, "type": "image_url"}
        response = await self.media.request_json(
            "POST",
            "images/edits",
            payload=payload,
            operation="image editing",
        )
        return _image_parts(response.data, payload["model"])


def _generation_payload(args: dict[str, Any], prompt: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(args.get("model") or DEFAULT_GROK_IMAGE_MODEL),
        "prompt": prompt,
        "response_format": "b64_json",
    }
    n = int(args.get("n", 1))
    if not 1 <= n <= 10:
        raise ValueError("n must be between 1 and 10")
    payload["n"] = n
    for key in ("aspect_ratio", "resolution", "quality"):
        value = args.get(key)
        if value not in (None, "", "auto"):
            payload[key] = value
    return payload


def _image_parts(data: dict[str, Any], model: str) -> list[ImagePart]:
    items = data.get("data")
    if not isinstance(items, list):
        raise ValueError("xAI image response did not contain data")
    parts: list[ImagePart] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            continue
        mime = str(item.get("mime_type") or "image/jpeg")
        part = ImagePart(
            url=f"data:{mime};base64,{encoded}",
            source_type="generated",
            source_name=f"{model}-{index + 1}",
        )
        revised = item.get("revised_prompt")
        if isinstance(revised, str) and revised:
            setattr(part, "revised_prompt", revised)
        parts.append(part)
    if not parts:
        raise ValueError("xAI image response did not contain base64 image data")
    return parts


__all__ = [
    "DEFAULT_GROK_IMAGE_MODEL",
    "GROK_IMAGE_MODEL_SUGGESTIONS",
    "GrokImageClient",
]
