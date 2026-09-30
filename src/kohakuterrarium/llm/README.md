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
| `turn_segments.py`     | Ordered reasoning/text/tool-call segment builder stored as `_kt_assistant_segments`                                      |
| `responses_ws.py`       | `ResponsesWSSession`: persistent Responses-API WebSocket transport with `previous_response_id` incremental continuation (shared by the openai + codex providers; enabled via `extra_body.websocket_mode`) |
| `anthropic_provider.py` | Native Anthropic Messages API provider using the official SDK (+ `anthropic_format.py`, `anthropic_pairing.py`, `anthropic_cache.py`) |
| `anthropic_images.py`   | Outgoing Anthropic image dimensions, including images nested in tool results; saved history is unchanged |
| `codex_provider.py`     | `CodexOAuthProvider`: ChatGPT subscription provider (+ `codex_format.py`, `codex_image_gen.py`, `codex_rate_limits.py`)   |
| `codex_image_budget.py` | Outbound image limits and recognition of explicit image-count rejections; saved history is unchanged |
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

## Codex image limits

The known Codex OAuth endpoint uses a 50-image request budget. Custom and
API-key Responses endpoints have no assumed image limit. When one rejects a
request with HTTP 400 and an explicit maximum image count, the provider learns
that limit for its current instance and retries once before any output. A new
model instance learns its own limit. WebSocket retries also respect submission
budgets and never replay after output or an uncertain transport failure.

Projection retains the newest permitted user/tool image references and replaces
omitted images with a count and a notice that they were not seen in this request.
This also applies when one message exceeds the limit; the notice requests smaller
batches if the omitted images are needed. Projection runs before artifact reads,
preserves text and tool pairing, and does not edit saved history. A changed image
projection invalidates WebSocket prefix matching and causes a full resend;
unchanged projections retain ordinary continuation and prompt-cache routing.

## Anthropic image dimensions

The [Anthropic vision limits](https://platform.claude.com/docs/en/build-with-claude/vision)
apply to the complete request, including prior turns and image blocks inside
tool results. Up to 20 images can have dimensions up to 8000 pixels. For larger
batches, each dimension is limited to 2000 pixels. Document blocks also count
toward the stricter threshold for compatibility with partner platforms.

The provider resizes oversized base64 images in outgoing requests while
preserving aspect ratio. Images already within the limit keep their original
encoded bytes. Stored conversation references and source files are unchanged.
Remote URLs and Files API references count toward the threshold but are not
fetched or resized locally. Request preparation runs off the event loop so
image resizing does not block other creatures.

## Dependencies

- `kohakuterrarium.core.registry` (Registry, for building tool schemas)
- `kohakuterrarium.utils.logging`
- Third-party: `httpx`, `openai` (optional), `anthropic` (optional), `litellm` (optional)
