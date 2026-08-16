"""
Route framework chat requests through the LiteLLM SDK.
"""

from typing import Any, AsyncIterator

import litellm

from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.openai_helpers import (
    delta_field,
    delta_field_present,
    merge_reasoning_detail_stream,
    pack_reasoning_fields,
)
from kohakuterrarium.llm.openai_sanitize import strip_internal_message_fields
from kohakuterrarium.llm.turn_segments import TurnSegmentsBuilder
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class LiteLLMProvider(BaseLLMProvider):
    """Provider adapter for LiteLLM's provider/model routing interface."""

    provider_name: str = "litellm"

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        api_key: str | None = None,
        config: LLMConfig | None = None,
        **kwargs: Any,
    ) -> None:
        effective_config = config or LLMConfig(model=model)
        if not effective_config.model:
            effective_config.model = model
        super().__init__(effective_config)
        self._api_key = api_key
        self._extra_kwargs = kwargs
        self._last_assistant_extra_fields: dict[str, Any] = {}

    def with_model(self, name: str) -> "LiteLLMProvider":
        if not name or name == self.config.model:
            return self
        new_config = LLMConfig(
            model=name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            top_p=self.config.top_p,
            stop=self.config.stop,
            extra=self.config.extra,
            retry_policy=self.config.retry_policy,
        )
        return LiteLLMProvider(
            model=name,
            api_key=self._api_key,
            config=new_config,
            **self._extra_kwargs,
        )

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        params = self._build_params(messages, tools=tools, stream=True, **kwargs)
        self._last_assistant_extra_fields = {}
        segments = TurnSegmentsBuilder()
        reasoning_text = ""
        reasoning_details: list[Any] = []
        reasoning_extra: dict[str, Any] = {}
        reasoning_text_seen = False
        reasoning_details_seen = False

        try:
            response = await litellm.acompletion(**params)

            pending_tool_calls: dict[int, dict[str, str]] = {}

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                if delta_field_present(delta, "reasoning_content"):
                    reasoning_text_seen = True
                rc_piece = delta_field(delta, "reasoning_content")
                if isinstance(rc_piece, str):
                    reasoning_text += rc_piece
                    segments.append_reasoning(rc_piece, source="reasoning_content")
                if delta_field_present(delta, "reasoning_details"):
                    reasoning_details_seen = True
                rd_piece = delta_field(delta, "reasoning_details")
                if isinstance(rd_piece, list):
                    for entry in rd_piece:
                        if isinstance(entry, dict):
                            merge_reasoning_detail_stream(reasoning_details, entry)
                            segments.append_reasoning(
                                entry.get("text") or entry.get("thinking") or "",
                                source="reasoning_details",
                                key=f"{entry.get('index')}:{entry.get('type')}",
                                signature=entry.get("signature"),
                            )
                if delta_field_present(delta, "reasoning"):
                    r_piece = delta_field(delta, "reasoning")
                    if isinstance(r_piece, str):
                        reasoning_extra["reasoning"] = (
                            reasoning_extra.get("reasoning", "") + r_piece
                        )
                        segments.append_reasoning(r_piece, source="reasoning")

                if delta.content:
                    segments.append_text(delta.content)
                    yield delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if hasattr(tc, "index") else 0
                        segments.append_tool_call_ref(tc.id or "", idx)
                        entry = pending_tool_calls.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments

            if (
                reasoning_text_seen
                or reasoning_details_seen
                or reasoning_text
                or reasoning_details
                or reasoning_extra
            ):
                self._last_assistant_extra_fields = pack_reasoning_fields(
                    reasoning_text,
                    reasoning_details,
                    reasoning_extra,
                    include_text=reasoning_text_seen,
                    include_details=reasoning_details_seen,
                )
                segments.finalize_tool_call_refs(pending_tool_calls)
                self._last_assistant_extra_fields = segments.inject_into(
                    self._last_assistant_extra_fields
                )

            self._last_tool_calls = [
                NativeToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in pending_tool_calls.values()
                if tc["name"]
            ]

        except Exception as e:
            logger.error("LiteLLM streaming error", error=str(e))
            raise

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        params = self._build_params(messages, stream=False, **kwargs)
        self._last_tool_calls = []
        self._last_assistant_extra_fields = {}

        try:
            response = await litellm.acompletion(**params)

            message = response.choices[0].message
            content = message.content or ""
            finish_reason = response.choices[0].finish_reason or "stop"

            usage = {}
            if hasattr(response, "usage") and response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }
            self._last_usage = usage

            extras = {}
            if delta_field_present(message, "reasoning_content"):
                rc = delta_field(message, "reasoning_content")
                if isinstance(rc, str):
                    extras["reasoning_content"] = rc
            if delta_field_present(message, "reasoning_details"):
                rd = delta_field(message, "reasoning_details")
                if isinstance(rd, list):
                    extras["reasoning_details"] = rd
            if delta_field_present(message, "reasoning"):
                reasoning = delta_field(message, "reasoning")
                if isinstance(reasoning, str):
                    extras["reasoning"] = reasoning
            self._last_assistant_extra_fields = extras

            segments = TurnSegmentsBuilder()
            if "reasoning_content" in extras:
                segments.append_reasoning(
                    extras["reasoning_content"], source="reasoning_content"
                )
            for entry in extras.get("reasoning_details") or []:
                if isinstance(entry, dict):
                    segments.append_reasoning(
                        entry.get("text") or entry.get("thinking") or "",
                        source="reasoning_details",
                        key=f"{entry.get('index')}:{entry.get('type')}",
                        signature=entry.get("signature"),
                    )
            if "reasoning" in extras:
                segments.append_reasoning(extras["reasoning"], source="reasoning")
            if message.content:
                segments.append_text(message.content)

            if hasattr(message, "tool_calls") and message.tool_calls:
                self._last_tool_calls = [
                    NativeToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                    for tc in message.tool_calls
                ]
                for tc in self._last_tool_calls:
                    segments.append_tool_call_ref(tc.id)
            self._last_assistant_extra_fields = segments.inject_into(
                self._last_assistant_extra_fields
            )

            return ChatResponse(
                content=content,
                finish_reason=finish_reason,
                usage=usage,
                model=response.model or self.config.model,
            )

        except Exception as e:
            logger.error("LiteLLM completion error", error=str(e))
            raise

    def _build_params(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.config.model,
            "messages": strip_internal_message_fields(messages),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "stream": stream,
            "drop_params": True,
        }

        if self._api_key:
            params["api_key"] = self._api_key

        max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        if self.config.stop:
            params["stop"] = self.config.stop

        if tools:
            params["tools"] = [t.to_api_format() for t in tools]
            params["tool_choice"] = "auto"

        return params
