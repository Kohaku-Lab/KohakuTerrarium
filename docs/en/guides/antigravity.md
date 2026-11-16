# Google Antigravity through a local agy login

This optional provider reuses a single **official agy consumer login on Windows or Linux**.
Sign in using `agy` first. KT does not start an OAuth flow, copy refresh tokens,
write an account token file, or log out the Google account.

```powershell
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-4-argon@reasoning=medium
```

`status` is offline. `refresh` asks agy to renew an expired access token when
needed. `models` makes an authenticated discovery request and does not generate
text. Generation consumes the account's available quota.

In Web settings, the OAuth sign-in section shows Google Antigravity with a
Check status action. This reads local credential state and respects the server's
admin-token setting. Expired credentials that agy can renew show Awaiting refresh;
renewal is attempted automatically on the next request. There is no manual renewal
button in Web settings. Provider settings do not display a discovered model list;
built-in and custom presets are managed in the Custom Models tab. CLI and Web
share the built-in model catalog and its reasoning variation selector. No preset becomes the default automatically.

## Models and reasoning

The presets describe the supported agy agent model families. The
server chooses that list by the client User-Agent, which KT sends in agy's
`antigravity/cli/<version> (...)` shape; any other shape gets a smaller catalog
and a 404 for models outside it. Limits are token limits, not account quota.

| Preset under `google-antigravity/` | Context choices | Output | Reasoning | Thinking sent |
| --- | --- | ---: | --- | --- |
| `gemini-4-hydrogen` | 256k (default), 1048k | 262,144 | low, medium, high, xhigh | `thinkingLevel` |
| `gemini-pro-orchestrator-g1-pro` | 256k (default), 1048k | 65,536 | high only | `thinkingLevel: HIGH`; fixed wire ID |
| `gemini-4-argon` | 256k (default), 512k, 900k | 65,536 | low, medium, high | `thinkingLevel` |
| `gemini-3.8-flash` | 256k (default), 1048k | 65,536 | low, medium, high | `thinkingBudget` 1,000 / 4,000 / dynamic |
| `claude-opus-5-5` | 256k (default), 840k | 128,000 | low, medium, high | `thinkingLevel` |
| `claude-sonnet-5-5` | 256k (default), 840k | 128,000 | low, medium, high | `thinkingLevel` |
| `gpt-oss-120b` | 80k (default), 131k | 32,768 | medium only | `thinkingBudget` 8,192 |

For model families, each effort is its own wire model, `<preset>-<effort>` (for example
`gemini-4-argon-high`), with the thinking setting the catalog lists for it. The
default family effort is **medium**. Hydrogen also accepts `@reasoning=xhigh`.
Select family effort with `@reasoning=low|medium|high` or the CLI/Web model picker.
Orchestrator uses its exact ID with HIGH thinking, without an effort suffix or
reasoning selector. Explicit IDs such as `claude-opus-5-5-low` are
accepted too, and an effort that conflicts with the ID fails before
authentication. Gemini 4 Argon requires a Google AI Ultra (or eligible
Enterprise) account. The catalog marks GPT-OSS for removal on 2026-11-02.

The `context` variation sets the profile's `max_context`, the history budget KT
compacts against. Hydrogen and Orchestrator use a conservative 256k default with the catalog's
1,048,576-token maximum available as `context=1048k`. Other defaults are agy's
compaction limits; the larger
choices are agy's Argon context picker (512k / 900k), agy's Claude long-context
limit (840k), and the catalog maximum (Flash 1048k, GPT-OSS 131k). The choice is
not sent upstream; a larger window costs more quota per turn only because each
request carries more input. Groups combine, for example
`google-antigravity/gemini-4-argon@reasoning=medium,context=512k`.

Retired presets resolve to their successor with a logged warning:
`gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3-flash` → `gemini-3.8-flash`;
`gemini-3.1-pro` → `gemini-4-argon`; `claude-sonnet-4-6` → `claude-sonnet-5-5`;
`claude-opus-4-6-thinking` → `claude-opus-5-5`. Raw retired wire IDs in an inline
config (`gemini-3.7-flash-tiered`, `gemini-pro-agent`, `gemini-3.1-pro-high`, …)
fail before authentication with `deprecated_model, use <successor>`. Unsupported
choices fail explicitly. Smaller profile output limits are retained; values above
the catalog cap, or at/below a positive thinking budget, are rejected. Custom model
IDs remain supported without inferred effort controls.

## Ownership and request behavior

The Windows adapter reads only `gemini:antigravity` in Credential Manager.
On Linux, install `kohakuterrarium[antigravity]` and run KT under the same user
and session D-Bus as agy, with an unlocked Secret Service keyring. KT searches
only the login collection (default alias if login is absent), matching
`service=gemini`, `username=antigravity`. It never creates or unlocks collections;
locked, duplicate, and unavailable stores fail explicitly. D-Bus replies share
a five-second deadline. A locked or unavailable store does not fall back to a file.
Both platforms also check the known fallback file `~/.gemini/antigravity-cli/antigravity-oauth-token`. If both
exist, resolve the conflict in agy before continuing. Only the consumer Bearer
schema is supported. Tokens remain in memory; the refresh operation is the
noninteractive, time-limited `agy --output-format json models` command. A local
lock serializes refreshes across KT processes, and concurrent callers share one
refresh within a process.

The transport is pinned to `https://daily-cloudcode-pa.googleapis.com`, using the
agy 1.5.7 header profile (changelist `1019769835`), with host OS and architecture.
Redirects are rejected.
Each request rereads credentials. Project discovery is checked against the token
used so that an account switch cannot silently combine a new token and an old
project. Errors omit raw upstream bodies and credential material. A 401 permits
one owner-managed rotation; transient failures retry only before any text,
reasoning, function call or signature has arrived.

## History and current limits

Signed response parts are retained in session state, bound to the wire model, managed
project, and current canonical message. Same-model tool calls, persistence,
event replay and resume preserve these parts. Claude replay assembles streamed
text/thinking blocks with their final signatures and removes empty placeholders;
Gemini retains its original parts. Editing a message invalidates its
old parts. Text history can be reused across models; tool history with missing or
incompatible signatures requires a new or compacted session and fails explicitly
instead of inventing a signature. Changing effort changes the wire model and
requires a new or compacted session for signed tool history. A family selector
and an explicit tier ID for the same wire model can reuse signed history.
Switching to OpenAI strips the internal Google state from requests.

This implementation supports local Windows and Linux CLI/Web operation.
Remote workers, multiple accounts, macOS credential stores, Linux without an
accessible Secret Service, arbitrary
endpoints, media generation, and arbitrary extra-body overrides are not supported.
Inline images are accepted; remote image URLs and unsupported content/schema types fail
explicitly. Discovery lists model IDs and is not a guarantee that every listed
model supports every modality.

Use of this optional integration remains subject to the account provider's terms
and restrictions. There is no compatibility or account-availability guarantee.

## Linux verification

Install with `python -m pip install -e '.[antigravity]'`, sign in with official
agy, and unlock the desktop keyring. Run `kt config antigravity status` (expected
`ready`, source `linux_secret_service`) and `kt config antigravity models`.
Model discovery does not generate text; a generation test uses account quota.
Use `google-antigravity/gemini-3.8-flash@reasoning=high` for a short smoke test.
Refresh testing requires an expired access token; refreshing a fresh token skips
agy and does not verify renewal.

With an eligible account, text-generation smoke tests also succeeded for
`gemini-4-argon-medium`, `gemini-4-hydrogen-high`, `gemini-4-hydrogen-xhigh`, and
`gemini-pro-orchestrator-g1-pro` (Orchestrator / Argon). Explicit XHIGH thinking
for Hydrogen and HIGH thinking for Orchestrator also passed text-generation
smoke tests. These checks do not validate Orchestrator's orchestration behavior. Existing presets, including
Gemini 4 Carbon, are preserved. Catalog visibility is not an account-access guarantee.

## Output limits and tool results

Per-call `max_tokens` overrides (including compaction summaries) leave the saved
profile unchanged and retain the total output cap. If a positive thinking budget
cannot fit, that request uses half the output cap, floored at 128; a dynamic
budget is sent unchanged. Caps that cannot exceed that minimum are rejected. Normal
requests retain the configured effort budget; unknown generation options still fail.
Tool results echo upstream function-call IDs when present. Locally generated IDs
for no-ID calls are used only for KT pairing and are not sent as upstream IDs.
