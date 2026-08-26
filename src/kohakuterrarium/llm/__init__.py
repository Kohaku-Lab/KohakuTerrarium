"""LLM provider, message, and tool-call public facade."""

import importlib

_EXPORTS = {
    "ANTHROPIC_BASE_URL": "kohakuterrarium.llm.anthropic_provider",
    "AnthropicProvider": "kohakuterrarium.llm.anthropic_provider",
    "AssistantMessage": "kohakuterrarium.llm.message",
    "BaseLLMProvider": "kohakuterrarium.llm.base",
    "ChatChunk": "kohakuterrarium.llm.base",
    "ChatResponse": "kohakuterrarium.llm.base",
    "CodexOAuthProvider": "kohakuterrarium.llm.codex_provider",
    "LLMConfig": "kohakuterrarium.llm.base",
    "LLMProvider": "kohakuterrarium.llm.base",
    "Message": "kohakuterrarium.llm.message",
    "MessageList": "kohakuterrarium.llm.message",
    "NativeToolCall": "kohakuterrarium.llm.base",
    "OPENAI_BASE_URL": "kohakuterrarium.llm.openai",
    "OPENROUTER_BASE_URL": "kohakuterrarium.llm.openai",
    "OpenAIProvider": "kohakuterrarium.llm.openai",
    "SystemMessage": "kohakuterrarium.llm.message",
    "ToolMessage": "kohakuterrarium.llm.message",
    "ToolSchema": "kohakuterrarium.llm.base",
    "UserMessage": "kohakuterrarium.llm.message",
    "create_message": "kohakuterrarium.llm.message",
    "dicts_to_messages": "kohakuterrarium.llm.message",
    "messages_to_dicts": "kohakuterrarium.llm.message",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        try:
            return importlib.import_module(f"kohakuterrarium.llm.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"kohakuterrarium.llm.{name}":
                raise
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from None
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
