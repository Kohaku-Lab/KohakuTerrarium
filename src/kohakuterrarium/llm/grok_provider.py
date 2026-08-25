"""OpenAI-compatible chat provider backed by reusable Grok OAuth access tokens."""

import asyncio
import hashlib
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.grok_auth import GrokToken, GrokTokens
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.recovery import RetryPolicy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class GrokSubscriptionProvider(OpenAIProvider):
    """Use Grok CLI or OpenCode OAuth access with xAI's compatible chat API."""

    provider_name = "grok-subscription"
    provider_native_tools = frozenset({"grok_image_gen", "video_gen"})

    def __init__(
        self,
        model: str = "grok-4.6",
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        max_retries: int = 2,
        retry_policy: RetryPolicy | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        candidates = GrokTokens.load_candidates()
        if not candidates:
            raise _missing_login_error()
        token = candidates[0]
        super().__init__(
            api_key=token.access_token,
            model=model,
            base_url=token.base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_headers=_chat_headers(token, model),
            extra_body=extra_body,
            max_retries=max_retries,
            retry_policy=retry_policy,
        )
        self._active_source = token.source
        self._active_fingerprint = _fingerprint(token)
        self._rejected_fingerprints: set[str] = set()

    @property
    def credential_source(self) -> str:
        """Return the redacted name of the currently active token owner."""
        return self._active_source

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Try ordered local credentials only before any response is emitted."""
        candidates = [
            token
            for token in GrokTokens.load_candidates()
            if _fingerprint(token) not in self._rejected_fingerprints
        ]
        if not candidates:
            raise _missing_login_error()

        last_error: Exception | None = None
        for index, token in enumerate(candidates):
            self._activate(token)
            emitted = False
            try:
                async for chunk in super()._stream_chat(
                    messages, tools=tools, **kwargs
                ):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:
                last_error = exc
                if emitted or _status_code(exc) != 401:
                    raise
                self._rejected_fingerprints.add(_fingerprint(token))
                if index + 1 >= len(candidates):
                    raise
                logger.warning(
                    "Grok credential rejected; trying next local source",
                    source=token.source,
                    status=_status_code(exc),
                )
        if last_error is not None:  # pragma: no cover - loop always returns/raises
            raise last_error

    def reload_credentials(self) -> bool:
        """Reread owner-managed auth files and activate a changed valid token."""
        for token in GrokTokens.load_candidates():
            fingerprint = _fingerprint(token)
            if fingerprint in self._rejected_fingerprints:
                continue
            if fingerprint == self._active_fingerprint:
                return False
            self._activate(token)
            return True
        return False

    def _prompt_cache_request_kwargs(self) -> dict[str, Any]:
        """Use xAI's documented Chat Completions conversation header."""
        if not self.prompt_cache_key:
            return {}
        return {"extra_headers": {"x-grok-conv-id": self.prompt_cache_key}}

    def with_model(self, name: str) -> "GrokSubscriptionProvider":
        """Return a Grok provider for another language model."""
        if not name or name == self.config.model:
            return self
        clone = GrokSubscriptionProvider(
            model=name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self._timeout,
            max_retries=self._max_retries,
            retry_policy=self._retry_policy,
            extra_body=dict(self.extra_body),
        )
        clone._rejected_fingerprints = set(self._rejected_fingerprints)
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone._profile_max_context = getattr(self, "_profile_max_context", None)
        return clone

    def _activate(self, token: GrokToken) -> None:
        fingerprint = _fingerprint(token)
        if fingerprint == self._active_fingerprint:
            return
        old_client = self._client
        old_session = self._ws_session
        self._ws_session = None
        self._api_key = token.access_token
        self._base_url_input = token.base_url
        self.base_url = token.base_url
        headers = _chat_headers(token, self.config.model)
        self._extra_headers = headers
        self._client = AsyncOpenAI(
            api_key=token.access_token,
            base_url=token.base_url,
            timeout=self._timeout,
            max_retries=self._max_retries,
            default_headers=headers,
        )
        self._active_source = token.source
        self._active_fingerprint = fingerprint
        try:
            loop = asyncio.get_running_loop()
            if old_session is not None:
                loop.create_task(old_session.close())
            loop.create_task(old_client.close())
        except RuntimeError:
            pass


def _fingerprint(token: GrokToken) -> str:
    headers = "\0".join(
        f"{name}={value}" for name, value in sorted(token.extra_headers.items())
    )
    raw = (
        f"{token.source}\0{token.access_token}\0{token.base_url}\0"
        f"{token.media_base_url}\0{headers}"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _chat_headers(token: GrokToken, model: str) -> dict[str, str]:
    headers = dict(token.extra_headers)
    if token.source == "grok-cli":
        headers.setdefault("X-XAI-Token-Auth", "xai-grok-cli")
        headers["x-grok-model-override"] = model
    return headers


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _missing_login_error() -> LLMNotConfiguredError:
    return LLMNotConfiguredError(
        "No usable Grok subscription login was found. Log in with Grok CLI "
        "or refresh the xAI login in OpenCode. KT does not fall back to "
        "XAI_API_KEY for the grok-subscription backend."
    )


__all__ = ["GrokSubscriptionProvider"]
