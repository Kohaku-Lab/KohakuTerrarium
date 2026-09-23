# Google Antigravity through a local agy login

This optional provider reuses a single **official agy consumer login on Windows**.
Sign in using `agy` first. KT does not start an OAuth flow, copy refresh tokens,
write an account token file, or log out the Google account.

```powershell
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-3-flash
```

`status` is offline. `refresh` asks agy to renew an expired access token when
needed. `models` makes an authenticated discovery request and does not generate
text. Generation consumes the account's available quota.

In Web settings, open the `google-antigravity` provider row to inspect local
status, refresh through agy, or fetch model IDs. These operations respect the
server's admin-token setting. Select one of the two built-in presets, or clone a
preset and paste an ID returned by discovery. The built-in presets are
`gemini-3-flash` and `claude-sonnet-4-6`; neither becomes the default automatically.
Their 120,000-token context and 8,192-token output limits are conservative KT
operating limits, not advertised account limits.

## Ownership and request behavior

The Windows adapter reads only `gemini:antigravity` in Credential Manager and the
known fallback file `~/.gemini/antigravity-cli/antigravity-oauth-token`. If both
exist, resolve the conflict in agy before continuing. Only the consumer Bearer
schema is supported. Tokens remain in memory; the refresh operation is the
noninteractive, time-limited `agy --output-format json models` command. A local
lock serializes refreshes across KT processes, and concurrent callers share one
refresh within a process.

The transport is pinned to `https://daily-cloudcode-pa.googleapis.com`, using the
agy 1.2.8 protocol profile validated by the experiment. Redirects are rejected.
Each request rereads credentials. Project discovery is checked against the token
used so that an account switch cannot silently combine a new token and an old
project. Errors omit raw upstream bodies and credential material. A 401 permits
one owner-managed rotation; transient failures retry only before any text,
reasoning, function call or signature has arrived.

## History and current limits

Signed response parts are retained in session state, bound to the model, managed
project, and current canonical message. Same-model tool calls, persistence,
event replay and resume preserve these parts. Editing a message invalidates its
old parts. Text history can be reused across models; tool history with missing or
incompatible signatures requires a new or compacted session and fails explicitly
instead of inventing a signature. Switching to OpenAI strips the internal Google
state from requests.

This first implementation supports local Windows CLI/Web operation only.
Remote workers, multiple accounts, macOS/Linux credential stores, arbitrary
endpoints, media generation, and explicit reasoning-effort/extra-body overrides
are not supported. The model's default thinking behavior is used. Inline images
are accepted; remote image URLs and unsupported content/schema types fail
explicitly. Discovery lists account IDs and is not a guarantee that every listed
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
