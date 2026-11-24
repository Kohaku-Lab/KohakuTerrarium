# Browser use: design

Date: 2026-11-24. Status: implemented (`browser/`, the `browser` plugin and
its six tools); this document describes what was built.

## Intent

Give a creature a real browser. Before this, the only browser capability was
`web_fetch`'s one-shot Crawl4AI backend: it renders a page and returns text,
with no session, no interaction and no state between calls. This design adds
an interactive, persistent Chromium session driven through function tools, so
a creature can log in to a test site, fill a form, click through a flow, and
verify a web UI it just built.

The surface is deliberately shaped by what other projects already learned the
hard way. The research behind each decision is summarised in "Evidence" below.

## Goals

- A small tool family that drives one long-lived Chromium per agent.
- Text-first observation (accessibility snapshot with element refs); pixels as
  a fallback.
- Network egress that cannot be steered to private addresses by a page, a
  redirect, a popup or a subresource.
- Page content always delivered as marked, untrusted data.
- Works with native and text tool formats, and with providers that require
  every argument (strict function calling), on Windows / macOS / Linux.
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
   once. It needs no change to the agent lifecycle beyond one optional plugin
   hook (`BasePlugin.on_disable`, called when a plugin is disabled at
   runtime), the session reaches sub-agents through `runtime_services`, and
   enabling or disabling the plugin adds or removes the tools. This is the
   shape `ui.apps` already uses.
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
| `browser_navigate` | egress | `action` = `goto` (default, opens `url`) / `back` / `forward` / `reload` (which ignore `url`). Returns the page header and the page text (the cleaned snapshot), or its interactive elements when it is longer than `PAGE_TEXT_MAX_CHARS` (8000). |
| `browser_snapshot` | read | Current page as a snapshot. Filters: `ref` (subtree), `depth` (0 = no limit), `query` (text match, keeps ancestors), `interactive` (interactive elements only); `boxes` adds `[box=x,y,w,h]` in viewport CSS pixels. |
| `browser_act` | mutate | `actions`: an ordered batch. Runs in order, stops at the first failure, reports the rest as not executed. Returns per-action status and a delta. `files`: paths for upload actions. |
| `browser_screenshot` | read | Viewport by default; `ref` for one element (wins over `full_page`); `full_page` capped in height; optional `path` to save. Returns an image part after a text line that stands alone. |
| `browser_tabs` | session | `list` (default) / `new` / `select` / `close` / `close_browser`. |
| `browser_eval` | script | Run JavaScript in the page, optionally against a `ref`. Returns JSON, marked untrusted. |

`browser_act` action kinds: `click` (ref, or x/y; button, double, modifiers),
`type` (ref; `text` or `secret`; `submit`; `slowly` for per-key events),
`select` (ref, `values` by label), `check` (ref, `checked`), `press` (key),
`hover` (ref, or x/y), `scroll`, `drag` (ref or x/y → `to` ref or
`to_x`/`to_y`), `upload` (ref, indexes into `files`), `wait` (`text` /
`text_gone` / `seconds`, capped at 10 s), `dialog` (`accept`, `text`). At
most 20 actions per batch.

One batch per call exists for two reasons. Tool calls from one turn run
concurrently in this framework, so two separate calls cannot express "click,
then type, and stop if the click failed". And each round trip to a reasoning
model is the dominant cost of a browsing task.

**Arguments.** In text tool formats every argument is a string. `actions`
therefore accepts a list, a JSON string, or the call body. For strict function
calling, every optional argument has a neutral value meaning "not given"
(`""`, `0`, `false`, `[]`; `goto` and `list` for the two enums), dropped
before parsing. Conflicts resolve by stated precedence: a non-empty `ref`
wins over coordinates (`0, 0` means none), `secret` over `text`, `wait` takes
`text`, then `text_gone`, then `seconds` above 0, and a screenshot `ref` over
`full_page`. A real value on another action kind's field is refused. A test
derives a fully filled call from each native schema.

**Results and errors.** Failures the page causes (stale ref, pending dialog,
failed navigation, a script that throws) are results marked as errors, with
the page's own text wrapped. A closed session, a failed launch, invalid
arguments and URLs the network policy refuses raise typed errors
(`browser/errors.py`, all `KTError`); the tool turns them into error results
whose fixed wording is outside the wrapper and whose page-derived detail is
inside it.

## Observation

**Snapshot.** Playwright's AI-mode ARIA snapshot (`aria_snapshot(mode="ai")`)
of the whole page, always taken page-wide and filtered in Python. A scoped
snapshot invalidates refs elsewhere on the page, so Playwright-side scoping is
never used. The raw text is cut at 1,000,000 characters and processed off the
event loop.

Post-processing, in order:

1. Redact configured secret values (`[secret:<name>]`) in the raw text.
2. Drop lines Playwright tags `[aria-hidden]`.
3. Drop non-interactive text that is visually hidden (opacity 0, font size
   under 2 px, text colour equal to background, positioned far off-screen,
   zero clip). Interactive elements are never dropped: screen-reader-only
   labels on real controls are legitimate. The count of dropped blocks is
   reported.
4. Redact the value of every password field (`(redacted)`); the same value
   elsewhere in a result's text becomes `[redacted]`.
5. Strip presentation noise. Keep a ref only where a model can use it:
   interactive elements (an interactive role, `[cursor=pointer]` or a
   checked / expanded / pressed / selected state), frames and dialogs,
   tables, grids, rows and articles, and named containers. In the baseline
   run every ref a model passed back was on one of these; unnamed `banner`,
   `main`, `list`, `navigation` and the like were never used. A hidden ref
   is still in the raw tree, so passing it back reads as "never shown".
   Fold a plain unnamed wrapper line into its children: a `generic` with
   one child or that is its parent's only child (as before), a `rowgroup`
   with one child, and a `listitem` or `paragraph` whose one child is
   interactive or holds no elements. A lifted line remembers the wrapper it
   came out of, so the delta places it as before and a wrapper that folds or
   unfolds between two reads moves nothing. Together these cut the page text
   of navigation results by 3.6% over the 29 benchmark start pages and two
   long generated pages; the interactive view is unchanged.
6. Apply `ref` / `depth` / `query` / `interactive` / `boxes`.
7. Cap at `snapshot_max_chars` (1000–1,000,000), ending with a line that says
   how much was cut and how to narrow.

Every result starts with a one-line header outside the wrapper: tab id, URL
and the kind of a pending dialog, nothing the page chose. The title is the
first line inside the wrapper. A later history stub still says where the
browser was.

**Page text after a new document.** `browser_navigate`, a `browser_act`
batch that lands on a new document, and a tab a batch opened all show the
page's cleaned snapshot when it fits `PAGE_TEXT_MAX_CHARS` (8000 characters,
never more than `snapshot_max_chars`), else its interactive elements plus a
fixed note that the text was left out and how to read it. The cap came from
the benchmark: every one of its 29 start pages fits (largest 6577, median
about 800), while a long article or a 60-item listing does not (12000 to
20000 and more). A model that sees the text skips a snapshot round; a page
over the cap would cost more tokens than that round saves.

**Delta after `browser_act`.** URL and title change, tabs opened (with the new
active tab's page text), dialogs, downloads, blocked requests, and the lines
added (`+`) to or changed (`~`) in the snapshot, with `(moved)` on elements
that changed place, capped at 40 lines and 3000 characters, or `No change in
the page snapshot.` A delta that was cut or removed more lines than it shows
gives way to the whole page when that fits the page-text cap; otherwise past
the cap the result says the page changed substantially and to take a
snapshot. An action that led to a blocked host is marked failed (`it led to
host:port, which the browser network policy blocked (reason); the page did
not load`) and the delta becomes `Navigation blocked: from → to; the page did
not load.`; its blocked record is not repeated under `Blocked N requests`. A
page with no nodes adds a fixed note that it may still be loading and to
wait.

**Refs.** Playwright's own: `e12`, and `f1e12` / `f2e12` once the tab has
loaded a new document or inside frames. An `iframe` line whose frame shows
another http(s) origin than the page carries `[origin=scheme://host:port]`,
redacted like any URL; the session reads the frame URLs of up to 20 iframes
per observation, and a failed read leaves the lines without it. Measured behaviour
that the session must compensate for:

- Acting on a stale ref waits the whole timeout before failing. The session
  checks `locator.count()` first and fails in a millisecond.
- Ref numbers can be reused inside an iframe after it navigates. The session
  keeps the role and name each ref had in the last result it returned,
  re-snapshots before a batch, and rejects a ref whose role or name changed,
  or which the model was never in a position to have seen.

A rejected ref returns a specific error naming the ref and telling the model
to take a snapshot.

**Untrusted marking.** Page-derived text is wrapped in a `page_content`
element carrying a per-session random id, and an `origin` attribute only
when the content's origin differs from that of the URL on the result's first
header line (a tab a batch opened, the tab list); lookalike
closing tags inside the content are escaped. The plugin contributes a short
`## Browser pages` prompt block stating that this content is data and never
instructions. This is defence in depth, not a guarantee: delimiting alone cut
measured attack success from 57.7% to 41.7% in AgentDojo.

**Screenshots.** Fixed viewport (default 1280×800), device scale factor 1 and
`scale="css"`, so screenshot pixels are CSS pixels and coordinate clicks do
not drift on HiDPI displays. PNG by default (measured smaller than JPEG on
text pages), JPEG when a PNG would exceed the per-image budget (about
500 KB); full pages stop at 8000 px. The text part names what was captured
and where it was saved. Text tool formats flatten tool results to text, so
there the image never reaches the model; the text says so.

## Session

One `BrowserSession` per agent, created by the plugin in `on_load`, shared by
the six tools and by the agent's sub-agents.

States: `new` → `launching` → `ready`; `ready` → `new` on idle close, crash,
an unresponsive browser or `close_browser` (the next call relaunches and says
state was lost); any → `closed`, which is terminal. A call that reaches a
closed session fails; it never relaunches. That matters because plugin unload
runs before in-flight tool jobs are cancelled.

- **Lock.** The session has its own FIFO lock. Sub-agents call tools directly
  and bypass the executor's serial lock.
- **Timeouts.** Every driver call is bounded. A hung page costs one action
  timeout, not thirty seconds. After a timeout the session sends the browser
  a 1 s round-trip probe; a browser that does not answer is killed and marked
  lost.
- **Cancellation.** On cancel the session releases the lock, marks its refs
  stale and re-raises. It does not tear the browser down.
- **Settling.** After an action: a short grace for a navigation to start,
  then DOM-mutation quiescence with a 150 ms quiet window and a 2 s cap.
  `networkidle` is not used. Quiescence cannot see pending timers, which is
  what the `wait` action is for. A read that a script-started navigation
  interrupts waits for the new document and retries once.
- **Dialogs.** A listener records each dialog. An action that opens one
  returns "dialog pending" instead of hanging. While a `confirm` or `prompt`
  is pending only the `dialog` action is accepted. `alert` and
  `beforeunload` are auto-accepted and reported.
- **Tabs.** Stable ids (`t1`, `t2`), at most 20; tabs a page opens beyond that
  are closed and reported. A tab opened by an action becomes the active tab
  and the result says so; when Chromium has announced a new window, the
  action waits up to 3 s for it to register.
- **Downloads.** Saved under `browser-downloads/` in the agent's working
  directory and reported with their path; the result waits up to 2 s for a
  download the batch started. At most 50 downloads and 1 GiB per browser.
  Playwright's own copies are deleted with the context.
- **Uploads.** Paths come from the top-level `files` argument so the sandbox
  path check sees them. A hidden file input has no ref, so the action falls
  back to the file-chooser path on the visible control.
- **Launch.** An explicit `executable_path` alone, else a `channel` alone,
  else Playwright's bundled Chromium, then system Chrome, then Edge. A fresh
  throwaway profile by default; `profile_dir` opts in to a persistent one. On
  Windows a non-Proactor event loop is rejected with a clear error before
  Playwright fails obscurely.
- **Close.** Set `closed`, then context, browser, Playwright, proxy, each with
  its own short timeout (5 s) so a stuck stage cannot consume the shutdown
  deadline; the whole close is bounded (`close_timeout`, 20 s), past which
  every browser still running is killed and teardown finishes in the
  background.
- **Process ids.** `process_ids()` reads the running browser's process ids
  (browser first) for tests and diagnostics; it never launches.

## Network policy

All browser traffic goes through a loopback filtering proxy owned by the
session. Playwright's `route()` is not used for policy: measured, it fires
only for the first URL of a redirect chain. The proxy sees every hop,
subresource, popup, service-worker request and WebSocket handshake. Chromium
sends loopback through it too (`<-loopback>`), QUIC is off, and WebRTC may not
send UDP outside it.

Per connection the proxy:

1. Accepts `http` and `https` only.
2. Applies `block_hosts`, then `allow_hosts` when it is non-empty. Entries are
   exact hosts, `*.` suffixes, optionally with a port.
3. Applies the sandbox plugin's network mode and allowlist when that plugin
   is active, read live so a runtime change takes effect.
4. Resolves the host, then rejects loopback unless `allow_loopback`, and
   private, carrier-grade NAT and unique-local addresses unless
   `allow_private`. Link-local addresses (which include cloud metadata
   endpoints), unspecified, multicast and reserved ranges are always
   rejected. An IP address listed in `block_hosts` also rejects any name that
   resolves to it.
5. Connects to an address it checked, so a second DNS answer cannot differ.
   Plain HTTP carries one request per connection.

A blocked request gets a 403 that names the reason, and blocked hosts are
listed in the next result. A navigation fails as blocked only when a host on
its redirect chain was refused; a blocked subresource does not fail it.

`browser_navigate` also rejects non-web schemes and URLs with embedded
credentials up front for a clear error.

Loopback is blocked by default. Testing a local dev server is a primary use,
and it is one option away: `allow_loopback: true`.

## Integration with existing plugins

- **sandbox.** `BaseTool` gains declarative `url_arguments`, `network_hosts`
  and `network_access`, next to the existing `path_arguments`; the sandbox
  checks them in addition to its built-in tool-name tables, as it already
  does for paths, so `web_fetch` / `web_search` are gated exactly as before
  and any other tool can opt in. The sandbox exposes its
  network policy as a runtime service for the proxy. With `network: deny` no
  browser tool runs, and open connections are dropped. The browser is treated
  as a network tool, not as an arbitrary subprocess, so it is not blocked by
  the `syscall` axis; it is refused when OS-level enforcement is required,
  because Chromium cannot be confined by the subprocess runner. The plugin's
  pre-hook (priority 0, before the sandbox) hands the sandbox `files` as a
  path list and drops an empty `files` or `path`, so a fully filled call is
  not checked as a write to the working directory.
- **permgate.** No code change. Documented configuration: `gated_tools`
  `browser_navigate`, `browser_act`, `browser_eval`, `browser_tabs`; leave
  `browser_snapshot` and `browser_screenshot` ungated.
- **Host resources.** When `host_resources_allowed` is false (a non-admin
  user's engine in multi-user mode) only a headless browser with a throwaway
  profile, the bundled Chromium and public addresses is permitted:
  `headless: false`, `profile_dir`, `executable_path`, `channel`,
  `allow_loopback` and `allow_private` are refused.
- **Runtime disable.** `BasePlugin.on_disable` closes the browser when the
  plugin is disabled; re-enabling it creates a fresh session.

## Secrets

Options hold references only (`secrets: {bank_pw: "env:BANK_PW"}`). A `type`
action names the secret; the value (at least four characters) is read from
the environment at execution and redacted as `[secret:<name>]` from every
text result, raw and URL- or JSON-encoded. Screenshots and `browser_eval` can
still observe a value; the docs say so and recommend gating `browser_eval`.

## Configuration

One entry:

```yaml
plugins:
  - name: browser
    options:
      headless: true
      allow_loopback: true
```

Options and defaults: `headless` (true), `channel`, `executable_path`,
`viewport` (1280x800), `profile_dir`, `allow_hosts`, `block_hosts`,
`allow_loopback` (false), `allow_private` (false), `secrets`,
`nav_timeout_s` (30), `action_timeout_s` (5), `snapshot_max_chars` (20000),
`idle_close_s` (600). Unknown options are refused. The plugin is registered
in the catalog and is off by default.

## Layout

```
src/kohakuterrarium/browser/           runtime; imports nothing from studio / api / cli
  options  policy  proxy  secrets      no Playwright
  snapshot  refs  hidden  results      no Playwright
  observe  images  errors  tasks       no Playwright
  actions  settle  dialogs  tabs       driver-protocol only
  downloads  launch  protocol
  session/                             BrowserSession pseudo-class package (README.md)
  driver                               the only module that imports Playwright
src/kohakuterrarium/builtins/plugins/browser/{plugin,gates}.py
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
  three argument shapes and fully filled native calls, result formatting, the
  session state machine against a scripted driver that implements the real
  driver protocol, plugin lifecycle (lazy launch, close on unload and
  disable, closed refuses relaunch, restart) and the sandbox wiring.
- **Browser-backed.** `tests/unit/browser/test_driver.py` (the driver behind a
  real session, crash recovery), the hidden-text probe, and one integration
  workflow (`tests/integration/test_browser.py`): an agent with the plugin and
  a scripted LLM against a loopback fixture site navigates, snapshots, acts,
  answers a dialog, works in a new tab, uploads, downloads, screenshots,
  evaluates, hits the network policy, is interrupted mid-action, is disabled
  and re-enabled, stops, and no browser process survives. They skip cleanly
  without Playwright or Chromium and share the `browser` xdist group.
- **Benchmark, real model, not in CI.** `scripts/browser_bench/`: 29 local
  micro-tasks, each with a programmatic check on server-side state,
  including infeasible tasks, injection canaries and egress probes, a
  scripted self-test proving each task sound, and a real-model harness that
  reports pass rate, false-success rate, steps, tokens and wall time over
  repeated runs, with harness errors counted separately from failures.

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

## Settled during the build

The open points this design left to the builder, as built: option defaults
as listed under Configuration; snapshot lines as Playwright's AI-mode lines
after noise stripping (`- role "name" [state] [ref=eN]: value`); delta caps
of 40 lines and 3000 characters; downloads under `browser-downloads/`; and
plain HTTP forwarded one request per connection with `Connection: close` on
both sides.
