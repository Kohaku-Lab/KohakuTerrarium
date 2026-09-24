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

In Web settings, open the `google-antigravity` provider row to inspect local
status, refresh through agy, or fetch model IDs. These operations respect the
server's admin-token setting. CLI and Web share the built-in model catalog and
its reasoning variation selector. No preset becomes the default automatically.

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
All Flash variants send the selected `thinkingLevel`. Pro routes to
`-low`/`-high` with budgets 1,001/10,001. Explicit tier IDs are also accepted;
an effort that conflicts with the ID fails before authentication.

agy 1.2.9 rejects `--effort` on both Claude models and rejects medium for Pro.
KT exposes the same choices. Claude sends the catalog's default thinking budget
of 1,024; it does not expose Anthropic direct-API effort controls. Unsupported
choices fail explicitly. Smaller output overrides are retained; values above the
catalog cap, or at/below a numeric thinking budget, are rejected.

The original `gemini-3-flash` preset remains for compatibility with its prior
120,000/8,192 operating limits and no effort override. Discovery can provide IDs
for custom presets; unknown models have no inferred effort controls.

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
event replay and resume preserve these parts. Editing a message invalidates its
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

The original live experiment verified agy credential refresh, project/model
discovery, Gemini 3 Flash signed tool roundtrip, and Claude Sonnet 4.6 text.
Implementation validation uses offline HTTP fixtures plus a real Terrarium
creature, scratchpad execution, persisted events and resumed sessions. It does
not constitute a fresh production-provider live inference test. Claude tool and
thinking combinations have not been live-tested.

The catalog/effort update additionally verifies every advertised Gemini tier's
outgoing request, Claude defaults, invalid settings, profile/variation resolution,
Web catalog metadata, and signed replay through a real Terrarium workflow. It does
not add live inference calls. A subsequent authorized two-request comparison
verified that Flash 3.8 `-high` returned HTTP 404 while `-tiered` with the same
HIGH thinking level returned HTTP 200 / STOP. Flash 3.7 routing follows the
same discovered tiered-only catalog but has not been live-tested. See the [metadata and routing evidence](../../zh-CN/dev/research/antigravity-agy-models-2026-09-24.md).
