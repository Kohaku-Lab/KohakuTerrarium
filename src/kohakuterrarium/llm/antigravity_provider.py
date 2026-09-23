"""Google Antigravity provider using locally borrowed agy access credentials."""

from uuid import uuid4

import httpx

from kohakuterrarium.llm.antigravity_auth import AgyCredentials, AntigravityError
from kohakuterrarium.llm.antigravity_client import (
    RequestError,
    backoff,
    check_status,
    headers,
    load_project,
    make_client,
    retryable,
    url,
)
from kohakuterrarium.llm.antigravity_format import (
    STATE_KEY,
    encode_messages,
    encode_tools,
    signed_state,
)
from kohakuterrarium.llm.antigravity_stream import Turn, read_events
from kohakuterrarium.llm.base import BaseLLMProvider, ChatResponse, LLMConfig
from kohakuterrarium.llm.recovery import RetryPolicy


class AntigravityProvider(BaseLLMProvider):
    """Stream text and signed function calls through the agy consumer protocol."""

    provider_name = "google-antigravity"

    def __init__(
        self,
        model: str,
        *,
        temperature=None,
        max_tokens=None,
        retry_policy=None,
        reasoning_effort="",
        extra_body=None,
        transport=None,
    ):
        if not model or not model.startswith(("gemini-", "claude-")):
            raise AntigravityError("unsupported_model")
        if extra_body:
            raise AntigravityError("extra_body_not_supported")
        if reasoning_effort:
            raise AntigravityError("reasoning_effort_not_supported_use_model_default")
        super().__init__(
            LLMConfig(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                retry_policy=retry_policy,
            )
        )
        self._transport = transport
        self._policy = RetryPolicy.from_value(retry_policy)
        self._finish_reason = ""

    def with_model(self, name):
        if not name or name == self.config.model:
            return self
        sibling = type(self)(
            name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            retry_policy=self.config.retry_policy,
            transport=self._transport,
        )
        for key in (
            "_profile_max_context",
            "_prompt_cache_enabled",
            "prompt_cache_key",
        ):
            if hasattr(self, key):
                setattr(sibling, key, getattr(self, key))
        sibling._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        sibling._overflow_rescue = self._overflow_rescue
        return sibling

    async def chat(
        self, messages, *, stream=True, tools=None, provider_native_tools=None, **kwargs
    ):
        normalized = self._normalize_messages(messages)
        iterator = self._stream_chat(
            normalized,
            tools=tools,
            provider_native_tools=provider_native_tools,
            **kwargs,
        )
        pieces = []
        try:
            async for piece in iterator:
                if stream:
                    yield piece
                else:
                    pieces.append(piece)
        finally:
            await iterator.aclose()
        if not stream:
            yield "".join(pieces)

    async def _complete_chat(self, messages, **kwargs):
        pieces = [piece async for piece in self._stream_chat(messages, **kwargs)]
        return ChatResponse(
            "".join(pieces), self._finish_reason, self.last_usage, self.config.model
        )

    def _request(self, messages, scope, tools, provider_native_tools, kwargs):
        if provider_native_tools or kwargs:
            raise AntigravityError("unsupported_generation_option")
        system, contents = encode_messages(messages, self.config.model, scope)
        if not contents:
            raise AntigravityError("empty_conversation")
        config = {"maxOutputTokens": self.config.max_tokens or 8192}
        if self.config.temperature is not None:
            config["temperature"] = self.config.temperature
        request = {"contents": contents, "generationConfig": config}
        if system["parts"]:
            request["systemInstruction"] = system
        if tools:
            request["tools"] = encode_tools(tools)
        return request

    async def _stream_chat(
        self, messages, *, tools=None, provider_native_tools=None, **kwargs
    ):
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_extra_fields = {}
        self._finish_reason = ""
        rejected, rotated = None, False
        async with make_client(self._transport) as client:
            for attempt in range(max(0, self._policy.max_retries) + 2):
                await AgyCredentials.ensure_fresh(rejected=rejected)
                rejected = None
                project, scope, token = await load_project(client, self._policy)
                request = self._request(
                    messages, scope, tools, provider_native_tools, kwargs
                )
                payload = {
                    "project": project,
                    "model": self.config.model,
                    "userPromptId": str(uuid4()),
                    "request": request,
                }
                turn = Turn()
                try:
                    async with client.stream(
                        "POST",
                        url("streamGenerateContent"),
                        headers=headers(token, self.config.model),
                        json=payload,
                    ) as response:
                        check_status(response)
                        async for event in read_events(response.aiter_lines()):
                            for piece in turn.feed(event):
                                yield piece
                    turn.finish()
                except (RequestError, httpx.TransportError) as exc:
                    if not turn.committed:
                        if (
                            isinstance(exc, RequestError)
                            and exc.status == 401
                            and not rotated
                        ):
                            rejected, rotated = token.fingerprint, True
                            continue
                        if attempt < self._policy.max_retries and retryable(
                            exc, self._policy
                        ):
                            await backoff(self._policy, attempt)
                            continue
                    if isinstance(exc, RequestError):
                        raise exc from None
                    raise AntigravityError("network_error") from None
                self._last_tool_calls = turn.calls
                self._last_usage = turn.usage
                self._finish_reason = turn.finish_reason
                message = {
                    "content": turn.text,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                        for call in turn.calls
                    ],
                }
                self._last_assistant_extra_fields = {
                    **turn.extra_fields(),
                    STATE_KEY: signed_state(
                        message, turn.parts, self.config.model, scope
                    ),
                }
                return
        raise AntigravityError("retry_exhausted")
