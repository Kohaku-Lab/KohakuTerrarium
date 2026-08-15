# llm/

LLM provider abstraction layer. Defines the `LLMProvider` protocol and
concrete implementations for OpenAI-compatible APIs, native Anthropic
(Messages API), Codex OAuth (ChatGPT subscription), and LiteLLM. All
providers support streaming chat, non-streaming completion, multimodal
messages, and native function calling via `ToolSchema`. The message module
provides typed message structures compatible with the OpenAI API format.

## Files

| File                    | Description                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`           | Re-exports all provider classes, message types, and tool schema utilities                                                 |
| `base.py`               | `LLMProvider` protocol, `BaseLLMProvider` ABC, `LLMConfig`, `ChatChunk`, `ChatResponse`, `ToolSchema`, `NativeToolCall`   |
| `openai.py`             | `OpenAIProvider`: OpenAI/OpenRouter/compatible API provider (+ `openai_helpers.py`, `openai_sanitize.py`, `openai_ws.py`) |
| `responses_reasoning.py` | Shared Responses-API reasoning-event collector (used by the Codex provider and OpenAI WebSocket path)                |
| `responses_ws.py`       | `ResponsesWSSession`: persistent Responses-API WebSocket transport with `previous_response_id` incremental continuation (shared by the openai + codex providers; enabled via `extra_body.websocket_mode`) |
| `anthropic_provider.py` | Native Anthropic Messages API provider using the official SDK (+ `anthropic_format.py`, `anthropic_pairing.py`, `anthropic_cache.py`) |
| `codex_provider.py`     | `CodexOAuthProvider`: ChatGPT subscription provider (+ `codex_format.py`, `codex_image_gen.py`, `codex_rate_limits.py`)   |
| `codex_auth.py`         | OAuth PKCE authentication flows (browser redirect and device code) with token caching                                     |
| `litellm_provider.py`   | LiteLLM provider (optional dep)                                                                                           |
| `deferred_provider.py`  | Placeholder provider for the "no model configured yet" state                                                              |
| `message.py`            | Typed message classes (`SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolMessage`) with multimodal content support |
| `tools.py`              | `build_tool_schemas`: converts registered tools into `ToolSchema` objects (+ `tool_schemas.py` builtin parameter schemas) |
| `presets.py`            | Built-in model presets, pure data (+ `preset_aliases.py`, `preset_store.py`)                                              |
| `backends.py`           | Backend (provider) persistence: YAML store shared with presets                                                           |
| `profile_types.py`      | `LLMBackend` / `LLMPreset` / `LLMProfile` dataclasses                                                                     |
| `profiles.py`           | Profile resolution + management                                                                                           |
| `variations.py`         | `name@group=option` variation-selector machinery                                                                          |
| `recovery.py`           | Provider-boundary recovery helpers for LLM calls                                                                          |
| `api_keys.py`           | API key storage and retrieval                                                                                             |

## Dependencies

- `kohakuterrarium.core.registry` (Registry, for building tool schemas)
- `kohakuterrarium.utils.logging`
- Third-party: `httpx`, `openai` (optional), `anthropic` (optional), `litellm` (optional)
