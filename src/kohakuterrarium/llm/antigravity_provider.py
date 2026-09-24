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
from kohakuterrarium.llm.antigravity_presets import MODELS, model_settings
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
        try:
            self._settings = model_settings(model, reasoning_effort, max_tokens)
        except ValueError as exc:
            raise AntigravityError(str(exc)) from None
        self.reasoning_effort = self._settings.reasoning_effort
        self._profile_max_context = self._settings.max_context
        super().__init__(
            LLMConfig(
                model=model,
                temperature=temperature,
                max_tokens=self._settings.max_output,
                retry_policy=retry_policy,
            )
        )
        self._transport = transport
        self._policy = RetryPolicy.from_value(retry_policy)
        self._finish_reason = ""

    def with_model(self, name):
        if not name or name == self.config.model:
            return self
        target = MODELS.get(name)
        effort = (
            self.reasoning_effort
            if target and self.reasoning_effort in target.efforts
            else ""
        )
        try:
            target_settings = model_settings(name)
        except ValueError as exc:
            raise AntigravityError(str(exc)) from None
        limit = min(self.config.max_tokens, target_settings.max_output)
        sibling = type(self)(
            name,
            temperature=self.config.temperature,
            max_tokens=limit,
            reasoning_effort=effort,
            retry_policy=self.config.retry_policy,
            transport=self._transport,
        )
        for key in (
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
        if provider_native_tools or kwargs.keys() - {"max_tokens"}:
            raise AntigravityError("unsupported_generation_option")
        system, contents = encode_messages(messages, self._settings.wire_model, scope)
        if not contents:
            raise AntigravityError("empty_conversation")
        limit = kwargs.get("max_tokens")
        thinking = dict(self._settings.thinking_config)
        if limit is None:
            limit = self.config.max_tokens
        else:
            ceiling = model_settings(
                self.config.model, self.reasoning_effort
            ).max_output
            if type(limit) is not int or not 0 < limit <= ceiling:
                raise AntigravityError("invalid_max_output_tokens")
            budget = thinking.get("thinkingBudget")
            if budget is not None and limit <= budget:
                minimum = (
                    1024 if self._settings.wire_model.startswith("claude-") else 128
                )
                if limit <= minimum:
                    raise AntigravityError(
                        "max_output_tokens_must_exceed_thinking_budget"
                    )
                thinking["thinkingBudget"] = max(minimum, limit // 2)
        config = {"maxOutputTokens": limit}
        if thinking:
            config["thinkingConfig"] = thinking
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
                    "model": self._settings.wire_model,
                    "userPromptId": str(uuid4()),
                    "request": request,
                }
                turn = Turn()
                request_headers = headers(token, self._settings.wire_model)
                if (
                    self._settings.wire_model.startswith("claude-")
                    and self._settings.thinking_config
                ):
                    request_headers["anthropic-beta"] = (
                        "interleaved-thinking-2025-05-14"
                    )
                try:
                    async with client.stream(
                        "POST",
                        url("streamGenerateContent"),
                        headers=request_headers,
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
                        message, turn.parts, self._settings.wire_model, scope
                    ),
                }
                return
        raise AntigravityError("retry_exhausted")
