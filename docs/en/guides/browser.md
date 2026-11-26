---
title: Browser
summary: Give a creature a real Chromium with the built-in browser plugin. Install, enable, the six browser tools, network policy, sandbox and permgate, secrets, limits.
tags:
  - guides
  - browser
  - plugins
  - tools
---

# Browser

For readers who want a creature to drive a real web browser: log in to a test
site, fill a form, click through a flow, or check a web UI it just built.

The built-in `browser` plugin gives a creature one long-lived Chromium and six
`browser_*` tools to drive it. The browser keeps its state between calls
(cookies, logins, open tabs), the model reads pages as text with element refs
and acts on those refs, and every request the page makes goes through a
filtering proxy that keeps it away from your private network.

It is different from `web_fetch`, which renders one page and returns its text
with no session and no interaction. Use `web_fetch` to read; use the browser
plugin when the creature has to do something on a site.

Concept primer: [plugins](../concepts/modules/plugin.md) and
[tools](../concepts/modules/tool.md). The plugin owns the browser and
contributes the tools; nothing about it is built into the framework core.

## Install

The browser is an optional extra. A framework install without it starts
normally; only the browser tools fail, with a message saying what to install.

```bash
pip install "kohakuterrarium[browser-use]"
playwright install chromium
```

The first line installs Playwright. The second downloads the Chromium build
Playwright drives. Without that download the plugin falls back to an installed
Google Chrome, then Microsoft Edge (see [Launch](#launch)), except for a
creature without host resources, which only ever runs the bundled Chromium. The
`kohakuterrarium[full]` extra includes `browser-use`; the older `browser` extra
is the Crawl4AI renderer behind `web_fetch` and does not install Playwright.

## Enable it on a creature

The plugin is registered in the built-in catalog and **off by default**: a
creature gets no browser tools until the plugin is on.

### In config

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true      # reach a dev server on localhost
```

With no `options` every default applies, and the browser can reach public web
sites only. The browser starts on the first browser call, not when the
creature starts.

### At runtime

The plugin is discovered for every creature, so it can be switched on in a
running session: `/plugin enable browser` from the chat, or the Plugins tab in
the web UI, which can also edit its options. Changed options take effect
on the next browser call: the current browser closes, and a new one starts with
the new options once the old one has finished closing (two browsers never run
on the same profile at once; see
[What the browser keeps](#what-the-browser-keeps-and-when-it-starts-over)).
Options changed while the plugin is disabled are kept and apply once it is
enabled again; no browser starts in between.

Disabling the plugin starts closing the browser at once. The tools disappear
from the creature, and enabling the plugin again starts a fresh browser on the
next call, after the old one has closed (no tabs, cookies or logins carried
over).

### From Python

Either put the entry in the config you build from, or pass a plugin instance:

```python
from kohakuterrarium import Agent
from kohakuterrarium.builtins.plugins.browser.plugin import BrowserPlugin

agent = await Agent.build(
    "path/to/creature",
    plugins=[BrowserPlugin(options={"allow_loopback": True})],
)
```

An `AgentConfig` with `plugins=[{"name": "browser", "options": {...}}]` works
the same way.

## The six tools

| Tool | Kind | What it is for |
|---|---|---|
| `browser_navigate` | egress | Open a `url` (`action` `goto`, the default), or go `back` / `forward` / `reload`, which ignore `url`. Returns the page header and the page text with refs (only the interactive elements when the page is longer than 8000 characters). |
| `browser_snapshot` | read | Read the current page as an accessibility tree with refs. Narrow it with `ref` (one subtree), `depth` (0 for no limit), `query` (lines matching a text, with their ancestors) or `interactive` (controls only); `boxes` adds each element's position in viewport pixels. |
| `browser_act` | mutate | Run an ordered batch of `actions` on one tab. Returns one status line per action and what changed on the page. `files` lists the paths that upload actions use. |
| `browser_screenshot` | read, file write | The viewport, one element (`ref`, which wins over `full_page`) or the whole page (`full_page`) as an image; `path` also saves it to a file. |
| `browser_tabs` | session, egress | `list` (the default), `new` (optionally with a `url`, which it opens), `select` or `close` a tab, or `close_browser`. |
| `browser_eval` | script | Run a JavaScript expression or function in the page, optionally on one element `ref`, and return its JSON result. |

Every tool except `browser_tabs` takes an optional `tab` (a tab id such as
`t2`; default the active tab). The tools are split by risk so that name-based
gates (`permgate`, `sandbox`) can tell reading a page from changing it or
running script in it. What each one can do beyond its main job:

- `browser_snapshot` and `browser_screenshot` start the browser when none is
  running (only `browser_tabs` with `list` never does).
- `browser_screenshot` with `path` writes an image file; the sandbox checks
  that path as a write.
- `browser_tabs` `new` with a `url` opens that page, like `browser_navigate`.
- `browser_eval` runs any script with the page's privileges: it can read
  everything the page holds, change the page, and send requests (through the
  same filtering proxy as the page). It is not a read-only tool.

Models that must fill in every argument (strict function calling) can send an
empty value for each one they do not use: `""`, `0`, `false` or `[]` means
not given.

### Actions in a batch

`browser_act` takes a list of action objects. Each has a `kind` and the fields
below.

| kind | Fields |
|---|---|
| `click` | `ref`, or `x` / `y` in viewport pixels; `button`, `double`, `modifiers` |
| `type` | `ref`; `text`, or `secret` (a configured secret name); `submit`, `slowly` |
| `select` | `ref`, `values` (option labels) |
| `check` | `ref`, `checked` (default true) |
| `press` | `key` such as `Enter` or `Control+A`; optional `ref` |
| `hover` | `ref`, or `x` / `y` |
| `scroll` | `ref` to scroll into view, or `dx` / `dy` pixels |
| `drag` | start `ref` or `x` / `y`; end `to` (a ref) or `to_x` / `to_y` |
| `upload` | `ref`, `files` (indexes into the call's `files` list) |
| `wait` | one of `text`, `text_gone`, `seconds` |
| `dialog` | `accept` (default true), `text` for a prompt |

Fields an action does not use may be sent empty. Where two could apply, a
non-empty `ref` wins over `x` / `y` (`0, 0` means none), `secret` wins over
`text` (an empty `text` clears the field), and `wait` takes `text`, else
`text_gone`, else `seconds` above 0. A real value on a field that belongs to
another kind is refused.

In text tool formats every argument arrives as a string, so `actions` may also
be a JSON string or the call body.

## How the tools fit together

The tools are built around one loop: **read the page, act by ref in
batches, read the result.**

1. **Open a page.** `browser_navigate` returns a header line (`[t1]` and the
   URL) and the page text, the same cleaned tree `browser_snapshot` gives,
   with a ref on each control. A page longer than 8000 characters shows only
   its interactive elements, with a note saying the text was left out.
2. **Read more when needed.** `browser_snapshot` gives the whole page:
   headings, text, controls. On a large page narrow it with `query` or
   `interactive` instead of reading everything. Refs look like `e12`; after
   the tab loads a new page they carry a prefix such as `f1e12` or `f2e12`,
   and elements inside a frame carry their frame's prefix. Use refs exactly
   as shown. Refs sit where they can be used: on interactive elements
   (including anything clickable or carrying a checked, expanded, pressed or
   selected state), frames, dialogs, tables, rows, articles and named
   containers; unnamed landmarks and wrappers such as `main` or `list` have
   none, and a ref that was never shown is refused. A frame showing another
   origin than the page names it on its line, as in
   `- iframe [ref=e12] [origin=https://pay.example]:`.
3. **Act in one batch.** Put the steps of one intent in one `browser_act`:
   type the name, pick the plan, tick the box, click Save, then `wait` for the
   confirmation text. Actions run in order and the batch stops at the first
   failure; the rest are reported as not executed. An action whose
   navigation the network policy blocked is a failure, and so is the action
   running when the batch reaches its time limit: 60 seconds, or three times
   (`action_timeout_s` + 10 s) when that is longer. One call per intent matters
   because tool calls from one turn run concurrently, so separate calls cannot
   express "click, then type, and stop if the click failed", and every round
   trip to the model is the slow part of a browsing task.
4. **Read the result.** After a batch on the same page the result is a
   delta: the lines added (`+`) or changed (`~`) since the snapshot before
   the batch, elements that changed place (marked `(moved)`), URL and title
   changes, tabs opened, dialogs, downloads and blocked requests, or `No
   change in the page snapshot.` When the delta would hide more than it shows
   (a filter that removed most rows, a closed overlay, a cut delta), the
   result shows the whole page instead. A batch that lands on a new page, or
   opens a tab, shows that page's text the way `browser_navigate` does. Check
   it for the outcome you wanted before reporting success.
5. **Long and lazy pages.** To reach the end of a long or lazily loaded page,
   `scroll` with a large `dy` (or `press` `End`), `wait` for the new items,
   and repeat until the result says `No change in the page snapshot.` A page
   that shows no content yet says it may still be loading; `wait` for the
   text you expect, then read it again.
6. **Fall back to pixels.** When something has no ref (a canvas, a chart, a
   custom drag area), `browser_screenshot` shows it, and `click`, `hover` and
   `drag` take `x` / `y` coordinates. Screenshot pixels are CSS pixels at a
   device scale of 1, so a coordinate read off the image is the coordinate to
   click. `browser_snapshot` with `boxes` gives the same coordinates as text.

Refs are valid for the page as it was last shown to the model. Before a batch
the session takes a fresh snapshot and rejects a ref whose element is gone or
now names a different element, at once and with a message naming the ref,
instead of waiting for a timeout. A ref inside a frame is also refused once
the frame has loaded another page, even when that page has an element with
the same role and name. Take a new snapshot and use the new refs.

### Tabs, dialogs, downloads, uploads

- **Tabs** have stable ids (`t1`, `t2`, …). A tab a click opens becomes the
  active tab, and the action's result says so and shows its text. When Chromium has announced a new window, the action waits up to
  3 seconds for it to open before returning. At most 20 tabs are open at
  once: `browser_tabs` refuses to open another and asks you to close one
  first, and tabs a page opens beyond the limit (or that a `profile_dir`
  restores at launch) are closed and reported.
- **Dialogs.** `alert` and `beforeunload` are accepted for you and reported.
  A `confirm` or `prompt` dialog leaves the call with "dialog pending"; until
  it is answered with a `dialog` action, every other browser call returns an
  error that says a dialog is waiting.
- **Downloads** are saved under `browser-downloads/` in the creature's working
  directory as it is when the download starts, and the result gives the saved
  path. With the sandbox on, a download whose path its file-write rules refuse
  is cancelled before anything is written and reported as refused, with the
  reason. A download still running
  after two seconds is reported as in progress. Over the session's lifetime,
  browser restarts included, at most 50 downloads are started and at most
  1 GB of downloaded files is kept. A download past the count is cancelled;
  one that would take the kept total over 1 GB is fetched in full and then
  deleted. Both are reported as failed, and so is a download still saving
  after 5 minutes, which is cancelled.
- **Uploads** take their paths from the call's `files` argument (relative to
  the working directory), so the sandbox checks them like any other file read.
  A file input that is hidden behind a styled button has no ref of its own;
  aim the `upload` at the visible button and the session goes through the file
  chooser.

### What the browser keeps, and when it starts over

The browser starts on the first browser call (`browser_tabs` with `list` does
not start it). It closes after `idle_close_s` without use, when it crashes,
or on `close_browser`; a browser that stops answering is killed together with
every process it started. The next call starts a fresh one and says that state
was lost. A fresh browser has a throwaway profile unless `profile_dir` is set,
so logins do not survive a restart.

With `profile_dir`, a browser starts only once every browser that ran on that
profile in this process is known to have exited: the processes it was started
with have ended, or its own close finished. A killed browser usually ends
within moments. When it cannot be confirmed gone within 5 seconds, the call
fails with an error naming the profile instead of starting a second browser on
it (two Chromium processes on one profile corrupt it), and a later call tries
again. This covers a restart inside the session, an options change and
disabling then enabling the plugin. It knows only browsers this process
started. A throwaway profile does not wait.

When the creature stops, or the plugin is unloaded or disabled, the browser
closes for good. A call that reaches a closed browser fails; it never starts a
new one behind your back.

The creature's sub-agents share its browser. Calls from the creature and its
sub-agents queue on the browser one at a time.

## Options

Every option, with its default. Unknown option names are rejected when the
creature is built.

| Option | Type | Default | Purpose |
|---|---|---|---|
| `headless` | bool | `true` | Run without a window. Set `false` to watch the browser work. |
| `channel` | string | none | Use an installed browser channel such as `chrome` or `msedge`. |
| `executable_path` | string | none | Path of a Chromium-based browser to run. |
| `viewport` | `"WIDTHxHEIGHT"` or `[w, h]` | `1280x800` | Page size in CSS pixels; width 320–7680, height 200–4320. |
| `profile_dir` | string | none | Persistent profile directory. None means a fresh throwaway profile each launch. |
| `allow_hosts` | list | `[]` | If not empty, the only hosts pages may reach. |
| `block_hosts` | list | `[]` | Hosts pages may never reach. Checked before `allow_hosts`. |
| `allow_loopback` | bool | `false` | Allow `localhost` and other loopback addresses (local dev servers). |
| `allow_private` | bool | `false` | Allow private-network addresses (10/8, 172.16/12, 192.168/16, carrier-grade NAT, IPv6 unique-local). |
| `secrets` | map | `{}` | Secret names, each to `{env: VAR_NAME, origins: [...]}`: the environment variable that holds it and the web origins it may be typed into, for `type` actions. See [Secrets](#secrets). |
| `nav_timeout_s` | number | `30` | Navigation timeout, in seconds (above 0, at most 3600). |
| `action_timeout_s` | number | `5` | Timeout of each action and page read, in seconds (above 0, at most 3600). A whole `browser_act` batch gets 60 s, or three times (this + 10 s) when that is longer. |
| `snapshot_max_chars` | int | `20000` | Cap on a snapshot's length (1000–1000000). A cut snapshot ends with a line saying how much was left out and how to narrow. |
| `idle_close_s` | number | `600` | Close the browser after this many seconds without a call (above 0, at most 86400). |

### Launch

With `executable_path`, only that browser is tried; with `channel`, only that
channel. With neither, the plugin tries Playwright's bundled Chromium, then an
installed Google Chrome, then Microsoft Edge. A creature without host resources
(see [Multi-user hosts](#multi-user-hosts)) tries the bundled Chromium only.
Pages always get a fixed viewport at a device scale factor of 1.

## Network policy

All browser traffic goes through a filtering proxy on 127.0.0.1 that the
session owns. Chromium is told to send even loopback traffic through it, QUIC
is turned off, and WebRTC may not send UDP outside the proxy. The proxy sees
every request: each hop of a redirect, subresources, popups, service-worker
requests and WebSocket handshakes, so a page cannot reach a blocked address by
redirecting or by loading it in the background.

For each connection the proxy:

1. accepts `http` and `https` only;
2. refuses a host in `block_hosts`, then, when `allow_hosts` is not empty, a
   host that is not in it;
3. asks the sandbox plugin, when it is active (see below);
4. resolves the host and refuses special addresses: loopback unless
   `allow_loopback`; private, carrier-grade NAT and IPv6 unique-local unless
   `allow_private`; and always link-local (which includes the
   `169.254.169.254` cloud metadata address), unspecified, multicast and
   reserved ranges. An IPv4 address carried inside an IPv6 one is judged as
   the IPv4 address;
5. connects to the address it checked, so a second DNS answer cannot point
   somewhere else.

A blocked request gets a 403 from the proxy, and the next tool result lists
it under `Blocked N requests` with the reason. A navigation whose main page
(or any host on its redirect chain) is blocked becomes a tool error that names
the URL and the reason. When a `browser_act` action leads to a blocked page
(a link or form that redirects to a blocked host), that action is reported
as failed, naming the host and the reason, the actions after it are not
run, and the result says `Navigation blocked` and that the page did not
load. `browser_navigate` also refuses non-web schemes
(`file:`, `javascript:` …) and URLs with embedded credentials up front.

**Local dev servers.** Loopback is blocked by default. Testing a local app is
one option away: `allow_loopback: true`. To keep the rest of the internet out
at the same time, add `allow_hosts`:

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["localhost", "127.0.0.1"]
```

**Host patterns.** `allow_hosts` and `block_hosts` entries are:

| Pattern | Matches |
|---|---|
| `example.com` | exactly `example.com`, any port |
| `*.example.com` | any subdomain of `example.com` (not `example.com` itself) |
| `example.com:8443` | `example.com` on port 8443 only |
| `127.0.0.1:5173` | that address on that port only |
| `[::1]:5173` | an IPv6 address with a port |

Names are compared lower-case and IDNA-encoded, with any trailing dot dropped.
A wildcard over an IP address is rejected. An IP address in `block_hosts` also
blocks a host name that resolves to it.

Link-local addresses are refused even when they are listed in `allow_hosts`;
no option permits them.

## With the sandbox

The browser follows the [`sandbox`](../reference/builtin-plugins.md#sandbox)
plugin when both are on, as the sandbox is at each moment: switched on or off,
and with its current options. No tool call is needed for a change to apply.

- **New connections** are judged by the sandbox's rules at the moment they
  open.
- **Open connections** are judged again every 2 seconds while any is open, and
  at once on each browser call. One whose host the rules now refuse is closed
  and listed as blocked in the next result, so a narrowed `network_allowlist`
  or a switch to `network: deny` closes the connections it refuses within about
  2 seconds.

- **`network: deny`**: no browser tool runs. The sandbox refuses the call
  before it reaches the browser.
- **`network_allowlist`**: the URL arguments of `browser_navigate` and
  `browser_tabs` are checked against it, and so is every connection the proxy
  opens, subresources included. The allowlist names hosts only (no ports). It
  applies on top of the browser's own `allow_hosts`; a host must pass both.
- **`os_enforce: require`**: the browser refuses to start (and closes a running
  one), because Chromium cannot run under the sandbox's subprocess
  confinement. The browser is treated as a network tool, so the `syscall`
  level does not block it.
- **File paths**: `files` in `browser_act` (read) and `path` in
  `browser_screenshot` (write) go through the same path checks as other file
  tools.
- **Downloads** are checked against the file-write rules (`fs_write`,
  `fs_deny`) before anything is created; a refused download is cancelled and
  reported as refused.
- **`backend: audit`** logs what it would refuse and lets it through.

## With permgate

[`permgate`](../reference/builtin-plugins.md#permgate) needs no
browser-specific setup. Gate the tools that
leave the page, change it or run script in it, and let the read-only ones
through:

```yaml
plugins:
  - name: browser
  - name: permgate
    options:
      gated_tools:
        - browser_navigate
        - browser_act
        - browser_eval
        - browser_tabs
```

`browser_tabs` is gated because `new` with a `url` opens a page.
`browser_snapshot` and `browser_screenshot` stay ungated here: they read the
page (and start the browser if none runs), and the screenshot `path` is a file
write the sandbox checks. An empty `gated_tools` gates every tool, so list
them.

`browser_eval` can change the page and send requests, not only read. To run
the plugin without it, turn that one tool off: `/module disable browser_eval`,
or `agent.set_tool_enabled("browser_eval", False)` from Python. It stays off
when the plugin is disabled and enabled again, and is saved with the session
(see [turning tools off](plugins.md#tool-origins-and-turning-tools-off)).

## Secrets

Options hold references, never values. Each secret names the environment
variable that holds it and the web origins it may be typed into:

```yaml
plugins:
  - name: browser
    options:
      secrets:
        staging_pw:
          env: STAGING_PASSWORD
          origins: ["https://staging.example.com"]
```

An origin is a scheme, a host and an optional port, such as
`https://staging.example.com` or `http://localhost:5173`. A path, a wildcard,
an empty list, or a secret without `origins` is a configuration error.

A `type` action that names `secret: staging_pw` (instead of `text`) types the
value of `STAGING_PASSWORD`, read from the environment when the action runs,
but only when the element's own document has one of the listed origins. A
frame counts on its own: a cross-origin frame inside an allowed page is
refused unless its origin is listed too. A refused action fails with a reason
that names the secret and the offending origin, never the value, for example
`refused: secret staging_pw may be typed only into https://staging.example.com;
e7 is on https://evil.example`.

The value must be at least four characters. In every text result it shows as
`[secret:staging_pw]`, raw and in its URL- and JSON-encoded spellings.
Password fields are redacted too: a password field's value shows as
`(redacted)` in snapshots (`[secret:staging_pw]` when the secret filled it),
and the same value appearing anywhere else in a result (a title, a URL) as
`[redacted]`. This also covers a field whose `autocomplete` names a password,
which a field switched to show its text usually keeps. Chromium's
accessibility tree carries password values in plain text, so when the check
that finds password fields fails on a page, every text field's value shows as
`(withheld)` and the result says so.

A creature without host resources (see [Multi-user hosts](#multi-user-hosts))
may not use `secrets`: they name environment variables of the server.

What can still see a secret:

- **Screenshots.** A page that echoes the value as visible text shows it in
  the image.
- **`browser_eval`.** A script runs with the page's privileges and can read a
  field's value. Gate `browser_eval` with `permgate`, or turn it off, when
  secrets are in play.
- **The site itself**, of course. Type secrets only into sites you trust.

## Untrusted page content

Everything a page chose (text, titles, element names, dialog messages, file
names, script results, error text from the page) is delivered inside a
`page_content` element that carries a random id fixed for the session and
`trust="untrusted"`, plus an `origin` attribute whenever the content comes
from another origin than the URL on the result's first line (a tab a click
opened, the tab list). Anything inside that looks like the
closing tag is escaped, so a page cannot end the wrapper early. The plugin adds
a short `## Browser pages` block to the system prompt saying that this content
is data, never instructions.

Snapshots also drop text a person would not see: elements marked
`aria-hidden`, and non-interactive text that is transparent, tiny, the same
colour as its background, far off-screen or clipped away. The result says how
many blocks were dropped. Interactive elements are always kept, since
screen-reader labels on real controls are legitimate. `browser_eval` reads the
raw page, so a script can still see hidden text; its output is wrapped the same
way.

This is **defence in depth, not a guarantee**. Marking content as untrusted
lowers the success rate of prompt injection; it does not stop it. A page can
still persuade a model. Limit what a browsing creature can reach and do: keep
`allow_hosts` tight, gate the mutating tools with `permgate`, and do not give
it secrets or tools it does not need.

## Multi-user hosts

On a server with user accounts, only administrators' engines may use host
resources. For every other user (and the anonymous slot), the browser runs
only headless, with a throwaway profile, the bundled Chromium, public
addresses and no secrets. A creature whose options ask for more
(`headless: false`, `profile_dir`, `executable_path`, `channel`,
`allow_loopback`, `allow_private` or `secrets`) gets an error on every browser
call naming the refused options, before any browser starts. Such a creature
never falls back to an installed Chrome or Edge: without the bundled Chromium
the error says to run `playwright install chromium`, and a browser started
before the restriction applied is closed at the next call. Single-user hosts,
`kt run` and the CLI are not restricted.

## Using the browser from your own tool or code

The browser runtime, `kohakuterrarium.browser`, is a component of its own; the
plugin and its six tools are one user of it. A custom tool, a script or a test
can use the same policed Chromium, with the same proxy and network policy,
without going through the tools. `import kohakuterrarium.browser` is cheap and
never imports Playwright; nothing starts until a call needs a browser.

There are three ways in:

- **The agent's shared browser.** From a tool, call
  `await browser.agent_session(context)` with the tool's `ToolContext`. It
  returns the session the `browser_*` tools use, after the plugin's own
  checks (sandbox, multi-user restrictions), or `None` when the plugin is not
  loaded; a refusal raises. Your calls queue with the tools' calls.
- **A session of your own.** `async with browser.BrowserSession(options) as
  session:` gives a browser no one else touches, closed when the block ends.
  Without a `downloads_dir` every download is cancelled, so nothing is written
  to disk.
- **A one-shot read.** `await browser.read_page(url, options)` starts a
  browser, reads the page and closes the browser before it returns.

`read_page`, on a session or one-shot, is the call for reading a page as
data. It returns a `PageRead` with the final `url` (after redirects), the
HTTP `status`, the `title`, `html` (the page's DOM after its scripts ran) and
`text` (the rendered text of the body, the way a person sees it laid out),
each cut at `max_chars` (1 000 000 by default). Configured secrets and the
values of the page's password fields are redacted from every field.
Hidden-text filtering is not applied, so treat the fields as untrusted page
content.

A read goes through the same URL check, proxy and network policy as
`browser_navigate`, and raises instead of returning an error text:
`NavigationBlockedError` for a refused URL or a blocked redirect,
`BrowserTimeoutError` for a load past `nav_timeout_s`, `NavigationError` for a
failed load or a URL that only downloads; a connection the proxy cuts during
the read because the sandbox now refuses it also raises
`NavigationBlockedError`. It runs in a temporary tab that the creature never
sees: it is not in the tab list, does not change the active tab or its refs,
and is closed afterwards on every path. Dialogs it opens are dismissed,
downloads it starts are cancelled, and windows it opens are closed; a window
one of the creature's tabs opens meanwhile becomes a tab as usual.

```python
from kohakuterrarium import browser

async def summarize_page(url: str, context) -> str:
    # Follow the sandbox when it is on: its network_policy service checks
    # every host the page connects to.
    policy = (context.runtime_services or {}).get("network_policy")
    page = await browser.read_page(
        url,
        browser.BrowserOptions(allow_hosts=("docs.example.com",)),
        external_check=policy.check if policy is not None else None,
    )
    return page.text
```

`external_check(host, port)` returns `None` to allow a connection or a reason
to refuse it, and is asked for every connection the proxy opens. The sandbox's
`network_policy` runtime service has that shape, so a tool follows the
sandbox without importing it. See [the Python reference](../reference/python.md#browser)
for every name and signature.

## Limits and known gaps

- At most 20 actions per `browser_act`; a `wait` lasts at most 10 seconds,
  and a batch at most 60 seconds (more with a long `action_timeout_s`).
- At most 20 open tabs; `browser_tabs` refuses to open another, and tabs a
  page opens (or a restored profile brings) beyond that are closed and
  reported.
- `browser_eval` results are cut at 10000 characters, and the script must
  finish within `action_timeout_s`.
- Screenshots are PNG, or JPEG when a PNG would exceed about 500 KB. An
  image still too big is downscaled, and the result gives the factor that
  turns image coordinates back into CSS pixels; one that would need a side
  under 200 pixels is not returned. Full-page and element screenshots stop
  at 8000 pixels high, and the result says so.
- After an action the session waits for the page to go quiet (150 ms without
  DOM changes, at most 2 seconds). It cannot see pending timers: when a page
  updates later, add a `wait` for the text you expect.
- **Screenshots reach the model only with native tool calling.** In the text
  tool formats (`bracket`, `xml`) a tool result is passed back as text, so the
  model gets the screenshot's text line (what was captured and where it was
  saved) but not the image. Use `tool_format: native` when screenshots
  matter, or save the screenshot with `path`.
- One browser per creature; its calls run one at a time.
- Chromium only, through Playwright.
- Not supported: attaching to your own running Chrome or its default profile;
  stealth, anti-bot evasion or CAPTCHA solving (a CAPTCHA or a two-factor
  prompt should be handed to the user); controlling the desktop or other
  applications.
- Not yet built: a dedicated browsing sub-agent, a prompt-injection classifier,
  provider-native computer-use tools, and memory of past browsing workflows.

## Example

[`examples/agent-apps/web_tester`](../../../examples/agent-apps/web_tester/)
is a small creature that checks a web app running on your machine. It turns on
the browser with `allow_loopback` and `allow_hosts` limited to `localhost` and
`127.0.0.1`, uses native tool calling so screenshots reach the model, and has a
prompt that asks it to report only what the page showed.

```bash
kt run examples/agent-apps/web_tester --llm <your preset>
```

## Troubleshooting

- **"the browser tools need Playwright, which is not installed".** Install
  the extra: `pip install "kohakuterrarium[browser-use]"`, then
  `playwright install chromium`.
- **"no usable Chromium was found".** Run `playwright install chromium`, or
  point `executable_path` or `channel` at an installed Chromium-based
  browser.
- **"the browser needs the Proactor event loop on Windows".** Playwright starts
  its driver as a subprocess, which only the Proactor loop supports. Something
  in the process set the selector loop: most often a server started with
  `--reload` (uvicorn runs the reloaded app on a selector loop on Windows), or
  code that sets `WindowsSelectorEventLoopPolicy`. Start the server without
  `--reload`, or remove the policy.
- **A navigation is blocked.** The error names the URL and the reason. For a
  local server set `allow_loopback: true` (and `allow_private: true` for a
  machine on your LAN); for a host missing from `allow_hosts`, add it; check
  the sandbox's `network_allowlist` too. Link-local addresses cannot be
  allowed.
- **"dialog pending" on every call.** A `confirm` or `prompt` dialog is open. Answer it with a `dialog` action in `browser_act` (`accept`
  true or false, `text` for a prompt).
- **"ref eN is no longer on the page" / "now points at a different element".**
  The page changed since the model last saw it. Take a snapshot and use the
  new refs.
- **"the browser tools need the browser plugin".** A `browser_*` tool was
  listed under `tools:` without the plugin. Enable the `browser` plugin
  instead; it brings all six tools.
- **"the browser session is closed".** The creature stopped or the plugin was
  disabled. Enable the plugin again; the next call starts a fresh browser.
- **"the browser profile … is in use by a browser that could not be confirmed
  to have exited".** A browser on that `profile_dir` was killed and its
  processes have not ended yet. Try again shortly; if it keeps failing, end the
  Chromium processes still using that profile directory.

## See also

- [Reference / built-in plugins](../reference/builtin-plugins.md#browser): the
  option table.
- [Reference / built-ins](../reference/builtins.md#browser): the six tools.
- [Plugins](plugins.md): how plugins hook into the agent, `sandbox` and
  `permgate` included.
- [Configuration](configuration.md): where `plugins:` sits in a creature
  config.
