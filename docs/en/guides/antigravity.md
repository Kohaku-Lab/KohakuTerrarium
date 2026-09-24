# Google Antigravity through a local agy login

This optional provider reuses a single **official agy consumer login on Windows**.
Sign in using `agy` first. KT does not start an OAuth flow, copy refresh tokens,
write an account token file, or log out the Google account.

```powershell
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-3.8-flash@reasoning=medium
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

Limits below match the agy 1.2.9 model catalog discovered on 2026-09-24. They
are token limits, not account quota; future server metadata can change.

| Preset under `google-antigravity/` | Context | Output | Reasoning choices |
| --- | ---: | ---: | --- |
| `gemini-3.6-flash` | 1,048,576 | 65,536 | low, medium, high |
| `gemini-3.7-flash` | 1,048,576 | 65,536 | low, medium, high |
| `gemini-3.8-flash` | 1,048,576 | 65,536 | low, medium, high |
| `gemini-3.1-pro` | 1,048,576 | 65,535 | low, high |
| `claude-sonnet-4-6` | 250,000 | 64,000 | Fixed Thinking |
| `claude-opus-4-6-thinking` | 250,000 | 64,000 | Fixed Thinking |

Use `@reasoning=low`, `@reasoning=medium`, or `@reasoning=high` where supported,
or select the same variation in the existing CLI/Web model picker. Gemini
presets default to **high** in KT. Flash 3.6 routes to the matching `-low`,
`-medium`, or `-high` model. Flash 3.7/3.8 use the discovered `-tiered` route.
All Flash variants send the selected `thinkingLevel`. Pro low routes to
`gemini-3.1-pro-low` and high to `gemini-pro-agent`, with budgets 1,001/10,001.
Explicit tier selectors (including `gemini-3.1-pro-high`) are also accepted;
an effort that conflicts with the ID fails before authentication.

agy 1.2.9 rejects `--effort` on both Claude models and rejects medium for Pro.
KT exposes the same choices. Claude sends the catalog's default thinking budget
of 1,024; it does not expose Anthropic direct-API effort controls. Unsupported
choices fail explicitly. Smaller profile output limits are retained; values above the
catalog cap, or at/below a numeric thinking budget, are rejected.

The retired `gemini-3-flash` is no longer included in the built-in Antigravity
presets. Choose a current Flash preset above for configurations that used it.
Custom model IDs remain supported without inferred effort controls.

## Ownership and request behavior

The Windows adapter reads only `gemini:antigravity` in Credential Manager and the
known fallback file `~/.gemini/antigravity-cli/antigravity-oauth-token`. If both
exist, resolve the conflict in agy before continuing. Only the consumer Bearer
schema is supported. Tokens remain in memory; the refresh operation is the
noninteractive, time-limited `agy --output-format json models` command. A local
lock serializes refreshes across KT processes, and concurrent callers share one
refresh within a process.

The transport is pinned to `https://daily-cloudcode-pa.googleapis.com`, using the
agy 1.2.9 header profile used for the latest metadata discovery. Redirects are rejected.
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
instead of inventing a signature. Changing effort on Flash 3.6 or Pro changes
the wire model and requires a new or compacted session for signed tool history.
Flash 3.7/3.8 share the same tiered route across efforts and retain the same
history binding. A family selector and an explicit tier ID for the same wire
model can reuse signed history. Switching to OpenAI strips the internal Google
state from requests.

This first implementation supports local Windows CLI/Web operation only.
Remote workers, multiple accounts, macOS/Linux credential stores, arbitrary
endpoints, media generation, and arbitrary extra-body overrides are not supported.
Inline images are accepted; remote image URLs and unsupported content/schema types fail
explicitly. Discovery lists model IDs and is not a guarantee that every listed
model supports every modality.

Use of this optional integration remains subject to the account provider's terms
and restrictions. There is no compatibility or account-availability guarantee.

## Validation evidence

The 2026-09-24 live matrix verified all 13 advertised model/effort combinations:
text, one side-effect-free echo call, and signed tool-result replay. Ten passed
initially; Pro high and both Claude models passed all three stages after fixes to
the Pro route and Claude streamed-history assembly. The matrix used the real KT
provider with small fixed prompts, caps of 2,048 (Pro high: 11,025), and no retries.
See the [live matrix and redacted evidence](../../zh-CN/dev/research/antigravity-online-matrix-2026-09-24.md)
and [catalog metadata](../../zh-CN/dev/research/antigravity-agy-models-2026-09-24.md).

Offline tests cover invalid settings, profile/variation resolution, Web catalog
metadata, and real Terrarium tool execution, persistence, resume and compaction.
The live matrix does not test full context/output limits, images, prolonged
sessions, or concurrent load; model limits above come from catalog metadata.

Per-call `max_tokens` overrides (including compaction summaries) leave the saved
profile unchanged and retain the total output cap. If a numeric thinking budget
cannot fit, that request uses half the output cap, floored at 128 for Gemini Pro
or 1,024 for Claude. Caps that cannot exceed that minimum are rejected. Normal
requests retain the configured effort budget; unknown generation options still fail.
Tool results echo upstream function-call IDs when present. Locally generated IDs
for no-ID calls are used only for KT pairing and are not sent as upstream IDs.
