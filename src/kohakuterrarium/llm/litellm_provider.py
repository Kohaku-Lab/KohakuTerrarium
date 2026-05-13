"""
LiteLLM provider for KohakuTerrarium.

Routes to 100+ LLM providers (OpenAI, Anthropic, Google, Azure, Bedrock,
Ollama, etc.) via the litellm SDK. No proxy server needed.

Model strings use the provider/model format, e.g.
anthropic/claude-sonnet-4-20250514, azure/gpt-4o, openai/gpt-4o.

See https://docs.litellm.ai/docs/providers for all supported models.
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
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class LiteLLMProvider(BaseLLMProvider):
    """LLM provider backed by the litellm SDK.

    Routes to 100+ providers through a single interface.

    Usage::

        provider = LiteLLMProvider(model="anthropic/claude-sonnet-4-20250514")

        async for chunk in provider.chat(messages):
            print(chunk, end="")
    """

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

        try:
            response = await litellm.acompletion(**params)

            pending_tool_calls: dict[int, dict[str, str]] = {}

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                if delta.content:
                    yield delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if hasattr(tc, "index") else 0
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

            if hasattr(message, "tool_calls") and message.tool_calls:
                self._last_tool_calls = [
                    NativeToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                    for tc in message.tool_calls
                ]

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
            "messages": messages,
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
