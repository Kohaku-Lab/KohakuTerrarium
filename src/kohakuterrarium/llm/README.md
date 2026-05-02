# llm/

LLM provider abstraction layer. Defines the `LLMProvider` protocol and
concrete implementations for OpenAI-compatible APIs and Codex OAuth
(ChatGPT subscription). All providers support streaming chat, non-streaming
completion, multimodal messages, and native function calling via `ToolSchema`.
The message module provides typed message structures compatible with the
OpenAI API format.

## Files

| File                | Description                                                                                                               |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`       | Re-exports all provider classes, message types, and tool schema utilities                                                 |
| `base.py`           | `LLMProvider` protocol, `BaseLLMProvider` ABC, `LLMConfig`, `ChatChunk`, `ChatResponse`, `ToolSchema`, `NativeToolCall`   |
| `openai.py`         | `OpenAIProvider`: OpenAI/OpenRouter/compatible API provider using httpx                                                   |
| `codex_provider.py` | `CodexOAuthProvider`: ChatGPT subscription provider using OpenAI SDK                                                      |
| `codex_auth.py`     | OAuth PKCE authentication flows (browser redirect and device code) with token caching                                     |
| `message.py`        | Typed message classes (`SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolMessage`) with multimodal content support |
| `tools.py`          | `build_tool_schemas`: converts registered tools into `ToolSchema` objects for native function calling                     |

## Dependencies

- `kohakuterrarium.core.registry` (Registry, for building tool schemas)
- `kohakuterrarium.utils.logging`
- Third-party: `httpx`, `openai` (optional, for CodexOAuthProvider)
