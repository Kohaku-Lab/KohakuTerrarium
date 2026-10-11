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
| `context_repair.py`     | `ContextRepair`: pure edits that remove refused media or a tool round, replayed on the request and the host conversation |
| `image_preparation.py`  | Cached image compression toward a per-image and whole-body byte target                                                    |
| `request_budget.py`     | `fit_request` (compress + measure a request body) and `RequestCeiling`, the byte ceiling learned from size refusals       |
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

## One provider per conversation

`BaseLLMProvider.fork()` returns a sibling for an independent conversation:
same model, credentials and SDK client; its own Responses WebSocket session,
per-turn result state (tool calls, usage, reasoning) and host hooks. Sub-agents
that inherit or override the parent model and the compaction fallback run on a
fork, so a sub-agent never breaks the parent's continuation, never forces it
onto HTTP while busy, and never reports recovery to the parent's conversation.
Closing a fork closes its WebSocket session only; the borrowed client stays
with the origin.

## Request size and frame ceiling

Codex, OpenAI (Chat Completions and Responses WebSocket) and Anthropic
requests compress inline images toward a whole-request byte target,
`extra_body.request_max_bytes` (default 15 MiB; 28 MB for Anthropic, whose
documented limit is 32 MB; `0` disables it). Every request is measured; saved
history is unchanged.

A request the server or a proxy refuses for its byte size is
`ErrorClass.OVERSIZE`: any HTTP 413 (JSON or an HTML proxy page) and the
wordings `payload too large`, `entity too large`, `content too large`,
`request_too_large`, `message too big`, OpenRouter's
`metadata.error_type: payload_too_large`. Token wording on a 413 keeps it
`OVERFLOW`. Before any output the provider answers it in order, each stage
bounded:

1. Lower the learned `RequestCeiling` to 3/4 of the refused request and refit
   the images under it (at most 3 times, and only while the request carries
   images and each refit came out smaller). The ceiling is shared by the
   provider, its forks and its `with_model` siblings and applies from then on,
   also when `request_max_bytes` is `0`; the target is the tighter of the two.
2. The content-repair ladder below.
3. Overflow recovery: host compaction, then an emergency tool-round drop.

A Responses WebSocket server that closes with 1009 (message too big) refused
the frame unread: the request was not submitted, the turn goes over HTTP, and
the session remembers the refused size as its frame ceiling. Later events over
the ceiling go straight to HTTP without touching the socket, until the
conversation fits again. `extra_body.websocket_max_message_bytes` presets the
ceiling. `extra_body.websocket_fallback_after` (default 3, `0` = never) is the
number of consecutive WS turns abandoned for HTTP after which that
conversation stays on HTTP; a completed WS turn resets the count.

## Content rejection recovery

`ErrorClass.CONTENT` covers rejections of request content that are neither
token overflow nor body size: invalid, unsupported, too small, too large or too
many images, image dimensions, a field over its length limit
(`string_above_max_length`, `string_too_long`), and the matching OpenRouter
`metadata.error_type` values. Codex, OpenAI-compatible and Anthropic
providers answer it, before any output, with a ladder of `ContextRepair`
edits, each tried once per request: strip the newest media-bearing message,
strip all media, drop the newest tool round. Removed content is replaced by a
note carrying the provider's error, so the model learns what happened and can
read the file again smaller. Each applied edit is reported through
`on_context_repair`; `Agent` and `SubAgent` replay it on their conversation,
keeping message identity, so the next turn does not resend the refused
content. WebSocket transport failures and failures after output began never
take this path, since the request may already have run.

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
