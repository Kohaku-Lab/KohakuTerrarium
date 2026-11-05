"""Codex Images endpoints: standalone image generation and editing.

Posts the request shape of the Codex CLI's image-generation extension to
``{codex base}/images/generations`` and ``/images/edits`` with the ChatGPT
subscription login. The backend takes the prompt, background, GIF controls and
input images; it chooses size, quality and the still-image format itself.
"""

import asyncio
from typing import Any

import httpx

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.codex.auth import CodexTokens, refresh_tokens
from kohakuterrarium.llm.codex.image.budget import CODEX_BASE_URL
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

IMAGE_MODEL = "gpt-image-2"
MAX_EDIT_IMAGES = 5
GIF_FRAMES = (2, 16)
GIF_FRAME_MS = (20, 2000)
IMAGEGEN_REQUEST_ID_HEADER = "x-codex-imagegen-request-id"
_LOGIN_HINT = "Codex subscription is not connected. Run: kt login codex"

_refresh_lock = asyncio.Lock()


class CodexImageError(RuntimeError):
    """An image request the backend refused or could not complete."""


def image_request(
    prompt: str,
    *,
    transparent_background: bool = False,
    gif: dict[str, int] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """The JSON body of a generation (no ``images``) or edit request."""
    body: dict[str, Any] = {}
    if images is not None:
        body["images"] = [{"image_url": url} for url in images]
    body.update(
        prompt=prompt,
        background="transparent" if transparent_background else "opaque",
        model=IMAGE_MODEL,
        quality="auto",
        size="auto",
    )
    if gif is not None:
        body["output_format"] = "gif"
        body.update(_gif_options(gif))
    return body


def _gif_options(gif: dict[str, int]) -> dict[str, int]:
    options: dict[str, int] = {}
    for key, (low, high) in (
        ("frame_count", GIF_FRAMES),
        ("frame_duration_ms", GIF_FRAME_MS),
    ):
        value = gif.get(key)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            raise ValueError(f"gif.{key} must be a whole number from {low} to {high}")
        options[key] = value
    return options


class CodexImageClient:
    """Generate or edit images with the Codex subscription."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Use the ChatGPT login, or ``api_key`` against a custom ``base_url``."""
        self.base_url = (base_url or CODEX_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    async def generate(
        self,
        prompt: str,
        *,
        transparent_background: bool = False,
        gif: dict[str, int] | None = None,
    ) -> list[ImagePart]:
        """Create images from ``prompt``."""
        body = image_request(
            _required_prompt(prompt),
            transparent_background=transparent_background,
            gif=gif,
        )
        return await self._post("images/generations", body, "image generation")

    async def edit(
        self,
        prompt: str,
        images: list[str],
        *,
        transparent_background: bool = False,
        gif: dict[str, int] | None = None,
    ) -> list[ImagePart]:
        """Edit ``images`` (``data:`` or ``https:`` URLs, at most five)."""
        if not 1 <= len(images) <= MAX_EDIT_IMAGES:
            raise ValueError(f"edit takes 1 to {MAX_EDIT_IMAGES} images")
        body = image_request(
            _required_prompt(prompt),
            transparent_background=transparent_background,
            gif=gif,
            images=images,
        )
        return await self._post("images/edits", body, "image edit")

    async def _post(
        self, path: str, body: dict[str, Any], operation: str
    ) -> list[ImagePart]:
        tokens = None if self.api_key else await _valid_tokens()
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, follow_redirects=False
        ) as client:
            response = await self._send(client, path, body, tokens)
            if response.status_code == 401 and tokens is not None:
                tokens = await _refreshed_tokens(tokens)
                response = await self._send(client, path, body, tokens)
        if response.status_code >= 400:
            raise CodexImageError(_failure(operation, response))
        return _image_parts(operation, response)

    async def _send(
        self,
        client: httpx.AsyncClient,
        path: str,
        body: dict[str, Any],
        tokens: CodexTokens | None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key or tokens.access_token}"}
        if tokens is not None and tokens.account_id:
            headers["chatgpt-account-id"] = tokens.account_id
        try:
            return await client.post(
                f"{self.base_url}/{path}", json=body, headers=headers
            )
        except httpx.TransportError as exc:
            raise CodexImageError(f"image request failed: {exc}") from exc


def _required_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("prompt is required")
    return text


async def _valid_tokens() -> CodexTokens:
    tokens = CodexTokens.load()
    if tokens is None:
        raise LLMNotConfiguredError(_LOGIN_HINT)
    if tokens.is_expired():
        tokens = await _refreshed_tokens(tokens)
    return tokens


async def _refreshed_tokens(stale: CodexTokens) -> CodexTokens:
    """Adopt a newer login another process saved, else refresh ``stale``."""
    async with _refresh_lock:
        current = CodexTokens.load()
        if (
            current is not None
            and not current.is_expired()
            and current.access_token != stale.access_token
        ):
            return current
        try:
            return await refresh_tokens(stale)
        except Exception as exc:
            raise LLMNotConfiguredError(
                "Codex subscription login expired. Run: kt login codex"
            ) from exc


def _failure(operation: str, response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    message = ""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        message = message or str(data.get("detail") or "")
    text = f"{operation} failed (HTTP {response.status_code})"
    if message:
        text += f": {message}"
    request_id = response.headers.get(IMAGEGEN_REQUEST_ID_HEADER)
    return f"{text} [imagegen request {request_id}]" if request_id else text


def _image_parts(operation: str, response: httpx.Response) -> list[ImagePart]:
    try:
        data = response.json()
    except ValueError as exc:
        raise CodexImageError(f"{operation} returned a body that is not JSON") from exc
    items = data.get("data") if isinstance(data, dict) else None
    fmt = (
        str(data.get("output_format") or "png").lower()
        if isinstance(data, dict)
        else "png"
    )
    parts = [
        ImagePart(
            url=f"data:image/{'jpeg' if fmt == 'jpg' else fmt};base64,{item['b64_json']}",
            detail="auto",
            source_type="image_gen",
            source_name=item.get("generation_id"),
        )
        for item in items or []
        if isinstance(item, dict) and item.get("b64_json")
    ]
    if not parts:
        raise CodexImageError(f"{operation} returned no image")
    return parts
