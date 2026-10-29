# Chat widgets

A **widget** is interactive UI an agent puts into the conversation: an app in
the canvas, a card in the chat, a form in the side panel. Widgets are drawn by
**renderers** in the web UI and talk back to the agent with messages.

Two optional builtin plugins let the agent answer with more than text:

| Plugin | How the agent uses it | Renderer | What the agent writes |
|---|---|---|---|
| `ui.declarative` | inline, in every reply | `kt.ui` | `<ui>` component tags mixed with Markdown |
| `ui.apps` | the `show_app` tool | `kt.app` | one HTML document, run in a sandboxed frame |

Inline components are the everyday form: tables, charts, diagrams, stats and
small calculators written straight into the reply. `show_app` is the
full-page option for when inline UI is not enough: large interactions,
simulations, games and complex showcases.

This guide covers:

1. [Presenting your answer](#presenting-your-answer)
2. [Inline UI](#inline-ui)
3. [Instant apps (`show_app`)](#instant-apps-show_app)
4. [What the canvas shows](#what-the-canvas-shows)
5. [For package authors](#for-package-authors)

## Presenting your answer

With either plugin on and a client that can draw it, the system prompt gets a
`## Presenting your answer` section right after the creature's own prompt. It
opens with "This chat shows more than text. Before you answer, choose the
form that serves the user best.", then one part per plugin, then "Plain
Markdown is for short answers and prose."

- `ui.declarative` adds the inline-components part while a client reports
  `kt.ui`: when to use each component, the markup, every component's props
  and one example.
- `ui.apps` adds the full-page part while `show_app` is visible (a client
  reports `kt.app`).

The section follows the attached clients: it changes when a browser connects
or leaves, and is absent when no part applies. Plugins add parts through
`BasePlugin.presentation_contribution` (see [Plugins](plugins.md)).

## Inline UI

### Turn it on

```yaml
plugins:
  - name: ui.declarative
```

The web UI reports `kt.ui` in its `client_hello`; the Rich CLI and the TUI
report it too and print blocks as text (see [In the terminal](#in-the-terminal)).
Headless runs and package outputs such as chat bots do not; there the section
is not in the prompt and the model writes plain Markdown.

### The markup

The model writes `<ui>` blocks between Markdown paragraphs:

```
Here is the split:

<ui title="Bill splitter">
  <state total={120} people={3} tip={10}/>
  <let each={round(total * (1 + tip / 100) / people, 2)}/>
  <grid columns={2}>
    <number label="Total" bind="total"/>
    <slider label="Tip %" bind="tip" min={0} max={30}/>
  </grid>
  <stat label="Each pays">{fmt(each, 2)}</stat>
  <button send="Split it so each pays {fmt(each, 2)}">Use this split</button>
</ui>
```

- A block starts with `<ui` at the start of a line, outside fenced code, and
  ends at the matching `</ui>`.
- `<state>` declares values inputs change (`bind`); `<let>` declares computed
  values, re-evaluated when state changes.
- `name="text"` may contain `{expr}`; `name={expr}` is a constant (number,
  list, `{key: value}` object) or an expression over state; a bare `name` is
  true. `{{` is a literal brace.
- Text between tags is content: Markdown for `text`, the value of `stat`,
  `badge` and `heading`, a button's label, and raw text for `code` and
  `diagram`.
- `send="…"` on a button or form sends that text as the user's next message.
  Everything else (sliders, tabs, toggles) runs in the browser without
  waking the agent.

The markup converts to the `kt.ui` v1 spec, so the same validator,
expression language and components serve both. Python and the frontend run
one conformance file, `tests/fixtures/kt_ui_markup.json`.

### Components

| Group | Types |
|---|---|
| Layout | `stack`, `grid`, `card`, `section`, `tabs` / `tab`, `divider` |
| Content | `text` (Markdown), `heading`, `badge`, `stat`, `kv`, `list`, `table`, `image`, `code` |
| Data | `chart` (`bar`, `line`, `area`, `pie`, `scatter`), `diagram` (Mermaid source) |
| Input | `number`, `slider`, `text_input`, `select`, `radio`, `toggle`, `checkbox`, `date` |
| Action | `button` (`set` changes state, `send` posts a message), `form` (`send` with the values its inputs bind) |
| Repeat | `each` (`items` expression, `as` name; adds `index`) |

Images accept only `data:image/...` URLs and same-origin paths, so a block
cannot load a remote tracking pixel.

### Expressions

Numbers, strings, `true` / `false` / `null`, lists, `{key: value}` objects;
names from state, `let` and `each`; `.key` and `[i]`; `+ - * / % **`,
comparisons, `and` / `or` / `not`, `a ? b : c`. Functions: `round floor ceil
abs min max sum avg len pow sqrt clamp fmt pct range join keys values str
num`. Limits: 2 KB per expression, 10 000 evaluation steps, ranges of at most
1000 items. Both sides pass `tests/fixtures/kt_ui_expressions.json`.

### When a block is wrong

A block that does not convert or validate shows as an error box with its
source; the rest of the reply is unaffected. On its next request the model
gets one short note naming each failed block and its errors, for example
`- block 2: root.children[0].value: is required`, so it can do better next
time without an extra turn.

### In the terminal

The Rich CLI (`kt run`) and the TUI print each closed block as its text form
(stats, tables, lists, a chart as its series, buttons as `[label]`) in a text
block. While a block streams they show a short "building UI" placeholder, not
the raw tags. `show_app` is not offered in terminals.

## Instant apps (`show_app`)

Enable the plugin on a creature:

```yaml
plugins:
  - name: ui.apps
    options:
      allowed_hosts: common   # default; [] = no network
      default_surface: canvas # or chat
      max_document_kb: 256
```

The tool, its technical guidance and its part of
[Presenting your answer](#presenting-your-answer) exist for the model only
while a client that renders `kt.app` is attached (the web UI announces its
renderers when it connects). A headless or CLI creature never sees
`show_app`.

### What an app can do

The app is one HTML document. Inside it, `window.kt` gives:

| Call | Meaning |
|---|---|
| `kt.data` | JSON the tool passed as `data` |
| `kt.state.get()` / `kt.state.set(patch)` | state kept with the app across reloads and resume |
| `kt.send(name, data)` | a message to the agent; needs a recent user click in the app |
| `kt.openLink(url)` | opens a link after the user confirms |
| `kt.on("theme" \| "data" \| "state", fn)` | changes pushed by the host |

Style apps with the dashboard tokens: `--kt-bg`, `--kt-fg`, `--kt-muted`,
`--kt-card`, `--kt-border`, `--kt-accent`, `--kt-font`, `--kt-mono`.

A message the tool is not waiting for reaches the agent as an ordinary
`widget_event` trigger, marked as untrusted content. With `wait_for`, the
tool call returns the first matching message instead, or a script error the
app hit while the call was waiting.

### Sandbox and network

- The frame is `sandbox="allow-scripts allow-forms"`: an opaque origin, so
  no cookies, no dashboard storage, no access to the parent page and no
  credentialed calls to the API.
- A Content-Security-Policy is placed before the app's own markup.
  `connect-src` is always `'none'`: an app cannot fetch, open WebSockets or
  send beacons. A policy the app declares itself can only narrow this one.
- `allowed_hosts` opens script, style, font and image loading from the
  listed hosts. Presets: `common` (jsDelivr, cdnjs, unpkg, esm.sh, Google
  Fonts, Tailwind Play CDN), `fonts`, `tailwind`. A list may mix presets and
  bare host names: `[common, cdn.example.org]`. These hosts serve almost any
  public package, so the list widens which code can run, never where data
  can go.

### Changing an app

`show_app(update=<id>, html=...)` replaces the document;
`show_app(update=<id>, patch=[{find, replace}])` edits it, each `find`
matching exactly once. After context compaction old documents are shortened
in the conversation, and a patch is refused until the agent calls
`show_app(read=<id>)` again. With a session store, documents are kept as
session artifacts (`apps/docs/<sha256>.html`), so `read` and `patch` work
after a resume.

## What the canvas shows

The canvas holds only:

- generated images (provider image output and `canvas_image`);
- canvas-surface widgets such as `show_app`;
- what the user pins: "Open in canvas" on a code block, or on a chat widget.

Long code blocks no longer open canvas tabs by themselves, and file previews
from `write` / `edit` / `multi_edit` show in the tool block. A pinned HTML
block runs as an app with no bridge and no allowed hosts.
`##canvas##` / `##artifact##` markers in assistant text still show for one
more release.

## For package authors

- **Backend.** A tool or plugin opens a widget through
  `context.widgets.open(renderer, spec, title=..., surface=..., fallback=...)`
  and gets a `WidgetHandle` (`update`, `close`, `wait`, `messages`, `state`).
  `open` raises `RendererUnavailable` when no attached client renders the id.
- **Visibility.** A tool that needs a renderer declares
  `requires_renderers = frozenset({"pkg.board"})`; it is hidden from the
  model, prompt included, while no attached client renders it.
- **Frontend.** Register a renderer with a `kind: widget` contribution and
  `kt.registerWidgetRenderer(id, component, {version, surfaces})` (see
  [UI extensions](ui-extensions.md)). The component gets `widget` (`id`,
  `spec`, `state`, `surface`, …) and `host` (`send`, `setState`,
  `openInCanvas`, `theme`).
- **Compaction.** A tool can shorten its own old calls by overriding
  `BaseTool.compact_arguments(args)`; the default keeps arguments unchanged.
