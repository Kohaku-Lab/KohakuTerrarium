# Browser use: design

Date: 2026-11-24. Status: proposed.

## Intent

Give a creature a real browser. Today the only browser capability is
`web_fetch`'s one-shot Crawl4AI backend: it renders a page and returns text,
with no session, no interaction and no state between calls. `ROADMAP.md` lists
computer use as future work. This design adds an interactive, persistent
Chromium session driven through function tools, so a creature can log in to a
test site, fill a form, click through a flow, and verify a web UI it just built.

The surface is deliberately shaped by what other projects already learned the
hard way. The research behind each decision is summarised in "Evidence" below.

## Goals

- A small tool family that drives one long-lived Chromium per agent.
- Text-first observation (accessibility snapshot with element refs); pixels as
  a fallback.
- Network egress that cannot be steered to private addresses by a page, a
  redirect, a popup or a subresource.
- Page content always delivered as marked, untrusted data.
- Works with native and text tool formats, on Windows / macOS / Linux.
- Playwright stays optional: a framework install without it starts normally.
- Deterministic tests that run without a browser in CI, browser-backed tests
  that run wherever Chromium exists, and a reproducible real-model benchmark.

## Non-goals

- Attaching to the user's own running Chrome or default profile.
- Stealth, anti-bot evasion, CAPTCHA solving. A CAPTCHA or 2FA prompt is
  reported to the user.
- Whole-desktop computer use (pixel-only control of arbitrary apps).
- Workflow memory or skill libraries for browsing.
- A dedicated `browse` sub-agent, an injection-classifier plugin, and
  provider-native computer-use tools. All three are follow-ups.

## Anti-goals

- A tool that reports success it did not verify.
- A network policy checked only on the first URL of a navigation.
- Secrets or password-field values reaching the model's context.
- A Chromium process that outlives the agent, or relaunches after shutdown.

## Approaches considered

1. **Six built-in tools owned by a built-in `browser` plugin (chosen).** The
   plugin contributes the tools, owns the session, and carries the options
   once. It needs no change to the agent lifecycle, the session reaches
   sub-agents through `runtime_services`, and enabling or disabling the plugin
   adds or removes the tools. This is the shape `ui.apps` already uses.
2. **Six tools listed in `tools:` plus a generic `BaseTool.close()` hook and a
   new stop stage.** Cleanup would sit after job cancellation, which is the
   better order, but options would have to be repeated on six entries or moved
   into a new `AgentConfig` field, and the framework lifecycle would change.
3. **MCP-style manager on the agent (`agent._browser_manager`) with a
   hardcoded stop stage.** Mirrors `mcp/`, but adds an agent attribute, a
   config field and a stage for one feature, and `context.agent` is `None`
   under `ExecutionBinding` hosts.
4. **Recommend an external Playwright MCP server instead of building this.**
   No code to maintain, but the egress policy, untrusted-content marking,
   secret redaction, sandbox and host-resource checks below cannot be enforced
   from outside, and MCP tool schemas cost more prompt than this surface.

A single `browser` tool with an `action` field was also considered and
rejected: name-based `permgate` and `sandbox` could not tell reading a page
from running script in it.

## Tool surface

Six tools, split by risk class so name-based gating is meaningful.

| Tool | Class | Purpose |
|------|-------|---------|
| `browser_navigate` | egress | `url`, or `action` = `back` / `forward` / `reload`. Returns the page header and a compact snapshot. |
| `browser_snapshot` | read | Current page as a snapshot. Filters: `ref` (subtree), `depth`, `query` (text match, keeps ancestors), `interactive` (interactive elements only). |
| `browser_act` | mutate | `actions`: an ordered batch. Runs in order, stops at the first failure, reports the rest as not executed. Returns per-action status and a delta. `files`: paths for upload actions. |
| `browser_screenshot` | read | Viewport by default; `ref` for one element; `full_page` capped in height; optional `path` to save. Returns an image part. |
| `browser_tabs` | session | `list` / `new` / `select` / `close` / `close_browser`. |
| `browser_eval` | script | Run JavaScript in the page, optionally against a `ref`. Returns JSON, marked untrusted. |

`browser_act` action kinds: `click` (ref, or x/y; button, double, modifiers),
`type` (ref; `text` or `secret`; `submit`; `slowly` for per-key events),
`select` (ref, `values` by label), `check` (ref, `checked`), `press` (key),
`hover`, `scroll`, `drag` (ref → `to`), `upload` (ref, indexes into `files`),
`wait` (`text` / `text_gone` / `seconds`, capped), `dialog` (`accept`, `text`).

One batch per call exists for two reasons. Tool calls from one turn run
concurrently in this framework, so two separate calls cannot express "click,
then type, and stop if the click failed". And each round trip to a reasoning
model is the dominant cost of a browsing task.

In text tool formats every argument is a string. `actions` therefore accepts a
list, a JSON string, or the call body.

## Observation

**Snapshot.** Playwright's AI-mode ARIA snapshot (`aria_snapshot(mode="ai")`)
of the whole page, always taken page-wide and filtered in Python. A scoped
snapshot invalidates refs elsewhere on the page, so Playwright-side scoping is
never used.

Post-processing, in order:

1. Drop lines Playwright tags `[aria-hidden]`.
2. Drop non-interactive text that is visually hidden (opacity 0, font size
   under 2 px, text colour equal to background, positioned far off-screen,
   zero clip). Interactive elements are never dropped: screen-reader-only
   labels on real controls are legitimate. The count of dropped blocks is
   reported.
3. Redact the value of every password field, and every configured secret
   value, wherever it appears.
4. Strip presentation noise and refs on roles that are neither interactive
   nor containers.
5. Apply `ref` / `depth` / `query` / `interactive`.
6. Cap at `snapshot_max_chars`, ending with a line that says how much was cut
   and how to narrow.

Every result starts with a one-line header (tab id, URL, title, pending
dialog), so a later history stub still says where the browser was.

**Delta after `browser_act`.** Not a full snapshot. URL and title change, tabs
opened, dialogs, downloads, blocked requests, and the lines added to or
removed from the snapshot, capped. Past the cap the result says the page
changed substantially and to take a snapshot.

**Refs.** Playwright's own (`e12`, `f1e3` inside frames). Measured behaviour
that the session must compensate for:

- Acting on a stale ref waits the whole timeout before failing. The session
  checks `locator.count()` first and fails in a millisecond.
- Ref numbers can be reused inside an iframe after it navigates. The session
  keeps the role and name each ref had in the last snapshot it returned from,
  re-snapshots before a batch, and rejects a ref whose role or name changed,
  or which the model was never in a position to have seen.

A rejected ref returns a specific error naming the ref and telling the model
to take a snapshot.

**Untrusted marking.** Page-derived text is wrapped in a `page_content`
element carrying the page origin and a per-session random id; lookalike
closing tags inside the content are escaped. The plugin contributes a short
prompt block stating that this content is data and never instructions. This
is defence in depth, not a guarantee: delimiting alone cut measured attack
success from 57.7% to 41.7% in AgentDojo.

**Screenshots.** Fixed viewport (default 1280×800), device scale factor 1 and
`scale="css"`, so screenshot pixels are CSS pixels and coordinate clicks do
not drift on HiDPI displays. PNG by default (measured smaller than JPEG on
text pages), JPEG when a PNG would exceed the per-image budget.

## Session

One `BrowserSession` per agent, created by the plugin in `on_load`, shared by
the six tools and by the agent's sub-agents.

States: `new` → `launching` → `ready`; `ready` → `new` on idle close or crash
(the next call relaunches and says state was lost); any → `closed`, which is
terminal. A call that reaches a closed session fails; it never relaunches.
That matters because plugin unload runs before in-flight tool jobs are
cancelled.

- **Lock.** The session has its own FIFO lock. Sub-agents call tools directly
  and bypass the executor's serial lock.
- **Timeouts.** Every driver call is bounded. A hung page costs one action
  timeout, not thirty seconds.
- **Cancellation.** On cancel the session releases the lock, marks its refs
  stale and re-raises. It does not tear the browser down.
- **Settling.** After an action: a short grace for a navigation to start,
  then DOM-mutation quiescence with a 150 ms quiet window and a 2 s cap.
  `networkidle` is not used. Quiescence cannot see pending timers, which is
  what the `wait` action is for.
- **Dialogs.** A listener records each dialog. An action that opens one
  returns "dialog pending" instead of hanging. While a dialog is pending only
  the `dialog` action is accepted. `alert` is auto-accepted and reported.
- **Tabs.** Stable ids (`t1`, `t2`). A tab opened by an action becomes the
  active tab and the result says so.
- **Downloads.** Saved under the agent's working directory and reported with
  their path; Playwright's own copies are deleted with the context.
- **Uploads.** Paths come from the top-level `files` argument so the sandbox
  path check sees them. A hidden file input has no ref, so the action falls
  back to the file-chooser path on the visible control.
- **Launch.** Explicit `executable_path`, then `channel`, then Playwright's
  bundled Chromium, then system Chrome or Edge. A fresh throwaway profile by
  default; `profile_dir` opts in to a persistent one. On Windows a
  non-Proactor event loop is rejected with a clear error before Playwright
  fails obscurely.
- **Close.** Set `closed`, then context, browser, Playwright, proxy, each with
  its own short timeout so a stuck stage cannot consume the shutdown deadline.

## Network policy

All browser traffic goes through a loopback filtering proxy owned by the
session. Playwright's `route()` is not used for policy: measured, it fires
only for the first URL of a redirect chain. The proxy sees every hop,
subresource, popup, service-worker request and WebSocket handshake.

Per connection the proxy:

1. Accepts `http` and `https` only.
2. Applies `block_hosts`, then `allow_hosts` when it is non-empty.
3. Applies the sandbox plugin's network mode and allowlist when that plugin
   is active, read live so a runtime change takes effect.
4. Resolves the host, then rejects loopback, private, carrier-grade NAT,
   unique-local and reserved addresses unless `allow_loopback` /
   `allow_private` permit them. Link-local addresses, which include cloud
   metadata endpoints, are always rejected.
5. Connects to the address it checked, so a second DNS answer cannot differ.

A blocked request gets a 403 that names the reason; a blocked main-frame
navigation becomes a tool error. Blocked hosts are listed in the next result.

`browser_navigate` also rejects non-web schemes up front for a clear error.

Loopback is blocked by default. Testing a local dev server is a primary use,
and it is one option away: `allow_loopback: true`.

## Integration with existing plugins

- **sandbox.** `BaseTool` gains declarative `url_arguments`, `network_hosts`
  and `network_access`, next to the existing `path_arguments`; the sandbox
  checks them in addition to its built-in tool-name tables, as it already
  does for paths, so `web_fetch` / `web_search` are gated exactly as before
  and any other tool can opt in. The sandbox exposes its
  network policy as a runtime service for the proxy. With `network: deny` no
  browser tool runs. The browser is treated as a network tool, not as an
  arbitrary subprocess, so it is not blocked by the `syscall` axis; it is
  refused when OS-level enforcement is required, because Chromium cannot be
  confined by the subprocess runner.
- **permgate.** No code change. Documented configuration: gate
  `browser_navigate`, `browser_act`, `browser_eval`, `browser_tabs`; allow
  `browser_snapshot` and `browser_screenshot`.
- **Host resources.** When `host_resources_allowed` is false (a non-admin
  user's engine in multi-user mode) only a headless throwaway profile is
  permitted: no `profile_dir`, no custom executable, no loopback or private
  access.

## Secrets

Options hold references only (`secrets: {bank_pw: "env:BANK_PW"}`). A `type`
action names the secret; the value is substituted at execution and redacted
from every text result. Screenshots and `browser_eval` can still observe a
value; the docs say so and recommend gating `browser_eval`.

## Configuration

One entry:

```yaml
plugins:
  - name: browser
    options:
      headless: true
      allow_loopback: true
```

Options: `headless`, `channel`, `executable_path`, `viewport`, `profile_dir`,
`allow_hosts`, `block_hosts`, `allow_loopback`, `allow_private`, `secrets`,
`nav_timeout_s`, `action_timeout_s`, `snapshot_max_chars`, `idle_close_s`.
The plugin is registered in the catalog and is off by default.

## Layout

```
src/kohakuterrarium/browser/           runtime; imports nothing from studio / api / cli
  options  policy  proxy  secrets      no Playwright
  snapshot  refs  hidden  results      no Playwright
  actions  settle  dialogs  tabs       driver-protocol only
  session  launch
  driver                               the only module that imports Playwright
src/kohakuterrarium/builtins/plugins/browser/plugin.py
src/kohakuterrarium/builtins/tools/browser/{navigate,snapshot,act,screenshot,tabs,eval,common}.py
src/kohakuterrarium/builtin_skills/tools/browser_*.md
```

`pyproject.toml` gains a `browser-use` extra (Playwright) included in `full`.
The existing `browser` extra keeps meaning Crawl4AI for `web_fetch`.

## Testing

- **Unit, no browser, runs in CI.** Policy (address classes, schemes, list
  logic), the proxy against loopback servers (blocked targets, redirect to a
  blocked address, CONNECT), snapshot processing on recorded snapshot text,
  ref bookkeeping, hidden-text and secret redaction, action parsing in all
  three argument shapes, result formatting, the session state machine against
  a scripted driver that implements the real driver protocol, plugin
  lifecycle (lazy launch, close on unload, closed refuses relaunch, restart).
- **Browser-backed.** One integration workflow: build an agent with the
  plugin and a scripted LLM against a loopback fixture site, then navigate,
  snapshot, act, hit a dialog, open a tab, upload, download, screenshot,
  eval, interrupt mid-action, stop, and assert the browser process is gone.
  Skips cleanly without Playwright or Chromium.
- **Benchmark, real model, not in CI.** `scripts/browser_bench/`: about 25
  local micro-tasks, each with a programmatic check on server-side state,
  including infeasible tasks, injection canaries and egress probes. Reports
  pass rate, false-success rate, steps, tokens and wall time over repeated
  runs, with harness errors counted separately from failures.

## Evidence

- Element refs beat coordinates: with the model fixed, coordinates cost up to
  28 points of task success; removing the tree cost at most 8.
- A smaller action set helps: AgentOccam went from 14.2% to 25.8% by trimming
  actions alone.
- Anthropic, OpenAI, Playwright MCP and Chrome DevTools MCP all converged on
  snapshot-with-refs first, screenshot second; on halting a batch at the
  first failure; and on bounded outputs.
- `route()`-only policy is the redirect bypass behind a published 2026 CVE.
- Hidden page text is the Comet attack vector.
- Most web-agent failures in a 2026 audit were false success claims, which is
  why the benchmark scores false success separately.

## Left to the builder

Exact option defaults, the precise snapshot line format after noise
stripping, the delta caps, the downloads directory name, and the proxy's
plain-HTTP forwarding strategy, each to be settled by measurement against the
fixture site and stated in the pull request.
