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

It is built for a trusted local user's agent. Its network confinement is the
filtering proxy, DNS pinning (each connection goes to the address that was
checked) and Chromium launch flags; Chromium runs as an ordinary process of
the server's user, with no OS-level process, filesystem or network isolation.
Its download caps bound the files a session keeps, not disk use or traffic
while a download runs, and Chromium's temporary download files, profile and
cache are outside them. A multi-tenant service, or a creature you do not
trust, needs a stronger boundary around the browser (a separate OS user,
container or VM per tenant, with its own storage quota and egress control).

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

## Ways to use it

The package itself is the **low-level API**: it applies the browser's own
options and whatever `external_check` the caller passes, and no framework
policy beyond that.

**A session of your own.** `async with BrowserSession(options) as session:`
gives a browser nobody else touches. Without `downloads_dir` every download
is cancelled and reported as refused, so nothing is written to disk.

**A one-shot read.** `await browser.read_page(url, options)` launches a
browser, reads, and closes it before returning.

In both, `external_check(host, port) -> reason | None` plugs an outside
network policy into the session's filtering proxy (the sandbox's
`network_policy` service has that shape). That adds the sandbox's network
rules only: it does not refuse when the sandbox requires OS confinement, does
not apply the host-resources rule (bundled Chromium only, no host options)
for a restricted creature, and does not consult the sandbox's write rules.

Framework code with a `ToolContext` uses one of the **framework-aware** ways,
which live in the builtins layer because their gates need the framework:

**A read under the plugin's gates.**
`await read_page_for(context, url, options)` from
`kohakuterrarium.builtins.plugins.browser.reading` reads in a browser of its
own and applies every gate the plugin applies to its own calls, whether or
not the plugin is loaded: OS confinement and host resources refuse before
anything launches, a restricted creature gets the bundled Chromium only, every
connection is judged by the sandbox's network rules (and refused while the
sandbox requires OS confinement), and no download is written. This is the
call for a tool such as `web_fetch`.

**The agent's shared browser.** The plugin publishes a `BrowserAccess` under
`runtime_services[BROWSER_SERVICE]`. A tool calls
`await browser.agent_session(context)` with its `ToolContext`: it gets the
agent's `BrowserSession` after the plugin's per-call gates (sandbox, host
resources), or `None` when the plugin is not loaded. A refusal of the gates
raises. When the sandbox starts requiring OS confinement while the browser
runs, its proxy refuses every new connection at once and closes open ones at
the next re-judge (within about 2 s); the next gated call closes the browser
and raises. The session is shared with the `browser_*` tools, so a read waits for
their calls and they wait for it, and it shares their browser state (below).

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
  exists). The tab is closed on every path, cancellation included; when the
  read gives up while the browser is still creating the tab (timeout or
  cancellation), the tab is closed once it appears and is never listed.
- It is not an isolated read. On a shared session the temporary tab uses the
  same browser profile as the agent's tabs: cookies, logins, same-origin
  storage, caches and service workers are common to both, so the read is sent
  the agent's cookies, and what the visited site does (sets a cookie,
  registers a service worker, changes server-side state) is there for the
  agent's tabs afterwards. A read that shares nothing uses a session of its
  own, the one-shot `read_page`, or `read_page_for`.
- Blocked requests are attributed to the read by endpoint, best effort: the
  filtering proxy sees connection targets, not pages, so a request it blocked
  while the read ran whose host and port are those of the read's URL, a
  redirect hop or the landing URL is taken as the read's: raised to the caller
  and left out of the agent's reports, even when an agent tab made it. Any
  other request blocked meanwhile, blocked subresources of the read's page
  included, stays for the agent's next result; requests blocked before the
  read began are never taken.
- Dialogs the read's page opens are dismissed at once; downloads it starts are
  cancelled and never saved. A window opened while a read runs is decided by
  its opener: one an agent tab opened (a timer firing between agent calls)
  becomes a tab as it would without the read; one the read's page opened, or
  one with no opener (`noopener`), is closed and never becomes a tab. So is a
  window the read's page announced that arrives within 3 s of a successful
  read.

## Ending browsers

Two Chromium processes on one persistent profile corrupt it, so a profile is
claimed per launch (`profiles`) and freed only when the browsers that ran on
it are known to have ended. The driver hands the claim its browser handle
(`EventSink.launch_started`) before Chromium can start, not when the launch
returns, so a launch that fails or is cancelled part-way keeps the profile
claimed. The handle counts as exited on evidence only: every process it holds
(recorded right after a persistent launch opens, before anything else can
fail) has ended; Playwright's own close returned (it waits for the browser);
or the Playwright driver exited with status 0, which it does only after ending
every browser it started (its exit handler kills those that did not close).
A driver that was killed or crashed proves nothing. A failed launch closes
what it opened, stops the driver, and kills what is still not known gone. A
launch that started nothing frees the profile as soon as its driver stops.

What stays unconfirmable: a browser that hung before its processes could be
read, whose driver then had to be killed. On Windows the driver's tree kill
ends it, but nothing can show that, so the profile stays refused until the
process restarts.

A forced kill can be slow (`taskkill /T /F` may take seconds per tree on
Windows), and the session's event loop is shared with every other agent and
tool, so `BrowserDriver.kill()` only starts the kill and returns an awaitable.
`processes.in_thread` runs the blocking part on a thread of its own, not a
daemon: a cancelled wait, a closing loop or interpreter exit leave it to
finish, and every step is bounded. The session waits for the kill where it
can afford to (a teardown, a hung browser), and `close()` past its bound only
starts it and returns. `BrowserProcesses` defers giving back its handles or
pidfds while a kill or a liveness check uses them, so a `release` (for
instance from a profile check seeing the browser gone) can never let a kill
reach a reused pid. With no running loop the kill runs in place.

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
- The plugin (`builtins/plugins/browser`, with its gates in `gates` and the
  framework-aware read in `reading`) and the tools (`builtins/tools/browser`)
  import `browser/`; nothing in `browser/` imports them. Tool-call wire
  details (argument schemas, text-format coercion) live with the tools.
