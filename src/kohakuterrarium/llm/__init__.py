"""
LLM module - Language model providers and abstractions.

Exports:
- LLMProvider: Protocol for LLM providers
- OpenAIProvider: OpenAI/OpenRouter compatible provider
- AnthropicProvider: Anthropic-compatible Messages API provider
- CodexOAuthProvider: ChatGPT subscription provider (Codex OAuth)
- Message types: Message, SystemMessage, UserMessage, AssistantMessage
- Native tool calling: ToolSchema, NativeToolCall, build_tool_schemas
"""

from kohakuterrarium.llm.anthropic_provider import ANTHROPIC_BASE_URL, AnthropicProvider
from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatChunk,
    ChatResponse,
    LLMConfig,
    LLMProvider,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.message import (
    AssistantMessage,
    Message,
    MessageList,
    SystemMessage,
    ToolMessage,
    UserMessage,
    create_message,
    dicts_to_messages,
    messages_to_dicts,
)
from kohakuterrarium.llm.openai import (
    OPENAI_BASE_URL,
    OPENROUTER_BASE_URL,
    OpenAIProvider,
)

# Note: build_tool_schemas is NOT imported here to avoid circular imports
# (llm.tools -> core.registry -> core.__init__ -> core.controller -> llm.tools).
# Import directly: from kohakuterrarium.llm.tools import build_tool_schemas

__all__ = [
    # Provider protocol
    "LLMProvider",
    "BaseLLMProvider",
    "LLMConfig",
    "ChatChunk",
    "ChatResponse",
    # Native tool calling
    "ToolSchema",
    "NativeToolCall",
    # OpenAI provider
    "OpenAIProvider",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    # Anthropic provider
    "AnthropicProvider",
    "ANTHROPIC_BASE_URL",
    # Codex OAuth provider
    "CodexOAuthProvider",
    # Message types
    "Message",
    "MessageList",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "create_message",
    "messages_to_dicts",
    "dicts_to_messages",
]
