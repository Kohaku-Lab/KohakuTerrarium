# browser

A policed Chromium that any part of the framework can use. The `browser`
plugin's six `browser_*` tools are one consumer; a tool such as `web_fetch`,
a script, or a test can use the same runtime without the plugin, without an
agent and without a sandbox.

The package depends on nothing in the framework except `kohakuterrarium.errors`
and `kohakuterrarium.utils` (`tests/unit/test_dep_graph_lint.py` enforces it,
counting imports inside functions and under `TYPE_CHECKING`). Playwright is
imported lazily by `driver.py` alone; `import kohakuterrarium.browser` loads no
submodule at all (the facade resolves names on first use, PEP 562).

## Public surface

`from kohakuterrarium import browser`, then:

| Name | What it is |
|---|---|
| `BrowserSession(options, *, downloads_dir=None, external_check=None, driver=None, ...)` | One lazily launched browser: FIFO operation lock, bounded calls, idle close, terminal `close()`; `async with` closes it. |
| `BrowserOptions` | Validated options (`BrowserOptions.from_mapping({...})` for plain data). |
| `session.read_page(url, *, max_chars=1_000_000) -> PageRead` | One page as plain data (below). |
| `read_page(url, options=None, *, external_check=None, max_chars=...) -> PageRead` | The same for callers without a session: launch, read, close. |
| `PageRead` | `url`, `status`, `title`, `html`, `text`, `html_truncated`, `text_truncated`. |
| `BrowserResult` | What the agent-facing operations (`navigate`, `snapshot`, `act`, …) return: text formatted for a model. |
| `BROWSER_SERVICE`, `BrowserAccess`, `browser_access(ctx)`, `agent_session(ctx)` | The contract by which a tool reaches the agent's shared session. |
| `BrowserError` and subclasses | Typed errors; all derive from `KTError`. |

## Three ways to use it

**The agent's shared browser.** The plugin publishes a `BrowserAccess` under
`runtime_services[BROWSER_SERVICE]`. A tool calls
`await browser.agent_session(context)` with its `ToolContext`: it gets the
agent's `BrowserSession` after the plugin's per-call gates (sandbox, host
resources), or `None` when the plugin is not loaded. A refusal of the gates
raises. The session is shared with the `browser_*` tools, so a read waits for
their calls and they wait for it.

**A session of your own.** `async with BrowserSession(options) as session:`
gives a browser nobody else touches. Without `downloads_dir` every download
is cancelled and reported as refused, so nothing is written to disk.

**A one-shot read.** `await browser.read_page(url, options)` launches a
browser, reads, and closes it before returning.

In each case `external_check(host, port) -> reason | None` plugs an outside
network policy into the session's filtering proxy. A tool running under the
sandbox passes the sandbox's `network_policy` runtime service, read from its
`ToolContext`, without importing the sandbox plugin:

```python
policy = (context.runtime_services or {}).get("network_policy")
check = policy.check if policy is not None else None
page = await browser.read_page(url, options, external_check=check)
```

## The structured read

`read_page` loads the URL in a temporary tab and returns:

- `url`: the final URL, after HTTP redirects and script navigations.
- `status`: the HTTP status of the response that answered the load (after
  HTTP redirects); `None` when no response did. A later script navigation does
  not change it.
- `title`: `document.title`, empty when it cannot be read; cut at `max_chars`.
- `html`: the doctype plus `document.documentElement.outerHTML` after
  scripts ran and the DOM settled (quiet for 150 ms, at most 2 s): the
  serialized DOM, not the bytes the server sent.
- `text`: `document.body.innerText` (the root's `textContent` when there is
  no body). This is the browser's rendered text of the main document: it
  leaves out `display: none` and `visibility: hidden` content, `<script>`,
  `<style>`, `<template>` and `<head>`, and follows the layout's line breaks.
  It does not include iframes or shadow roots, and it keeps text that is
  rendered but hard to see (off-screen, transparent, tiny, clipped): the
  hidden-text probe that cleans the agent's snapshots is **not** applied.
- `html_truncated` / `text_truncated`: whether that field was cut at
  `max_chars` (1 000 000 by default, at most 10 000 000; each field on its own).

What is filtered: every field has the session's configured secrets redacted
(`[secret:<name>]`, every encoded spelling), and so has every value of a
password field of the main document, whether the page wrote it as an
attribute or set it from a script or an autofill (`[redacted]`). A value the
size cut split is dropped from the end of the field. Values of other text
fields that a script set are not in `html` (the serialized DOM carries only
`value` attributes) nor in `text`. Nothing else is filtered: all of it is
page-chosen, untrusted text, and a consumer that hands it to a model wraps it
as untrusted itself.

How it behaves:

- The URL check, the filtering proxy and the network policy are those of
  `navigate`. A refused URL raises `NavigationBlockedError` before anything
  launches; a hop the proxy blocked (HTTP redirect or script navigation)
  raises it after the load. A load that overruns `nav_timeout_s` raises
  `BrowserTimeoutError`, a failed one `NavigationError` (also when the proxy
  could not reach the host, and when the URL only started a download), a page
  that never stops navigating `PageNavigatingError`. A closed session raises
  `BrowserClosedError`; a browser lost mid-read `BrowserStateLostError`.
  A connection the proxy cuts mid-read because the policy changed (it
  re-judges open connections) raises `NavigationBlockedError` with the
  policy's reason.
- It holds the session's operation lock, launches the browser if needed, and
  re-arms the idle close like any operation. A read that launches a browser
  after a loss leaves the "state was lost" notice to the agent's next call.
- The temporary tab is not an agent tab: it is not in the tab list, never
  active, records no refs, leaves every tab's last observation alone, and
  does not count toward the tab cap (reads are serialized, so at most one
  exists). Requests the proxy blocked during the read are dropped, not
  reported to the agent. The tab is closed on every path, cancellation
  included; when the read gives up while the browser is still creating the
  tab (timeout or cancellation), the tab is closed once it appears and is
  never listed.
- Dialogs the read's page opens are dismissed at once; downloads it starts are
  cancelled and never saved. A window opened while a read runs is decided by
  its opener: one an agent tab opened (a timer firing between agent calls)
  becomes a tab as it would without the read; one the read's page opened, or
  one with no opener (`noopener`), is closed and never becomes a tab. So is a
  window the read's page announced that arrives within 3 s of a successful
  read.

## Layers

The **runtime layer** knows browsers, networks and pages; it never formats
anything for a model:

| Module | Concern |
|---|---|
| `errors` | typed errors |
| `options`, `policy`, `secrets` | options, the network policy, secret references and redaction |
| `proxy`, `egress` | the filtering proxy every connection goes through; navigation endpoints and refusal reasons |
| `launch`, `protocol`, `driver` | the launch plan, the driver protocol, its Playwright implementation |
| `processes` | ending a browser's whole process tree when it no longer answers, and seeing that it ended |
| `profiles` | one browser per persistent profile: a launch waits for the last browser on it to be known gone, or refuses |
| `settle`, `tabs`, `tasks` | settling after a load or action, the tab registry, background futures |
| `downloads`, `dialogs` | download saving and caps; dialog bookkeeping |
| `session/types`, `session/sink`, `session/lifecycle` | states and limits, driver events, launch / lock / teardown / idle close |
| `session/reading`, `read` | the structured read and its one-shot helper |
| `access` | the contract for reaching the agent's session |

The **agent-facing layer** turns pages into text for a model:

| Module | Concern |
|---|---|
| `snapshot`, `hidden`, `observe`, `refs` | ARIA snapshots, hidden-text and password filtering, observations, element refs |
| `results`, `images` | result wording and the untrusted wrapper; screenshot sizing |
| `actions` | the `actions` batch of `browser_act`: parsing (including the neutral "not given" values a strict native tool call fills in, which nested action items need) and execution |
| `session/observing`, `navigation`, `acting`, `capture`, `tab_ops` | the `BrowserResult`-returning operations |

`session/assembly` (the `BrowserSession` class), `session/__init__` and the
package facade compose both layers.

Allowed import directions:

- Nothing in `browser/` imports framework modules other than
  `kohakuterrarium.errors` and `kohakuterrarium.utils`.
- The agent-facing layer may import the runtime layer; the runtime layer
  does not import the agent-facing layer. Two exceptions remain, listed in
  `tests/unit/browser/test_layers.py`: `session/lifecycle` creates and resets
  the `RefBook` (`refs`) and clears the per-tab `Observation`s (`observe`)
  when a browser is lost.
- The structured read (`session/reading`, `read`) imports runtime modules only.
- The plugin (`builtins/plugins/browser`) and the tools
  (`builtins/tools/browser`) import `browser/`; nothing in `browser/` imports
  them. Tool-call wire details (argument schemas, text-format coercion) live
  with the tools.
