# Chat widgets

A **widget** is interactive UI an agent puts into the conversation: an app in
the canvas, a card in the chat, a form in the side panel. Widgets are drawn by
**renderers** in the web UI and talk back to the agent with messages.

Two optional builtin plugins give the agent widget tools:

| Plugin | Tool | Renderer | What the agent writes |
|---|---|---|---|
| `ui.apps` | `show_app` | `kt.app` | one HTML document, run in a sandboxed frame |
| `ui.declarative` | `show_ui` | `kt.ui` | a JSON component tree, bindings and safe expressions |

This guide covers:

1. [Instant apps (`show_app`)](#instant-apps-show_app)
2. [What the canvas shows](#what-the-canvas-shows)
3. [For package authors](#for-package-authors)
4. [Declarative UI (`show_ui`)](#declarative-ui-show_ui)

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

The tool, and its prompt guidance, exist for the model only while a client
that renders `kt.app` is attached (the web UI announces its renderers when
it connects). A headless or CLI creature never sees `show_app`.

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
- canvas-surface widgets: `show_app`, and `show_ui` in page mode;
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

## Declarative UI (show_ui)

The `ui.declarative` plugin contributes `show_ui`. The model writes a
JSON spec, not code; the web UI draws it with built-in components in
the app theme. Expressions run in a small pure language (no `eval`),
so the format is safe for creatures that read untrusted input.

### Turn it on

```yaml
plugins:
  - name: ui.declarative
```

`show_ui` needs the `kt.ui` renderer. The web UI announces it in its
`client_hello` when it connects, so the tool (and its prompt guidance)
appears once a browser is attached and disappears when the last one
leaves. Headless runs, the terminal UIs and package outputs such as
chat bots do not report `kt.ui`; there the tool is not offered.

### A spec

```json
{
  "title": "Bill splitter",
  "state": {"total": 120, "people": 3, "tip": 10},
  "computed": {"each": "round(total * (1 + tip / 100) / people, 2)"},
  "root": {"type": "stack", "children": [
    {"type": "number", "label": "Total", "bind": "total"},
    {"type": "slider", "label": "Tip %", "bind": "tip", "min": 0, "max": 30},
    {"type": "number", "label": "People", "bind": "people", "min": 1},
    {"type": "stat", "label": "Each pays", "value": "{{ fmt(each, 2) }}"},
    {"type": "button", "label": "Send to chat", "send": "result",
     "data": {"each": "{{ each }}"}}
  ]}
}
```

- `state` holds named values; inputs change them through `bind`.
- `computed` holds named expressions, re-evaluated when state changes.
- Any string prop may contain `{{ expr }}`. A prop that is exactly one
  `{{ expr }}` keeps the value's type (a list stays a list).
- `layout: "page"` makes a larger page and opens on the canvas surface
  by default.

The calculator above runs entirely in the browser; the agent is not
woken while the user moves the slider.

### Components

| Group | Types |
|---|---|
| Layout | `stack`, `grid`, `card`, `section`, `tabs` / `tab`, `divider` |
| Content | `text` (markdown), `heading`, `badge`, `stat`, `kv`, `list`, `table`, `image`, `code` |
| Data | `chart` (`bar`, `line`, `area`, `pie`, `scatter`), `diagram` (shown as source) |
| Input | `number`, `slider`, `text_input`, `select`, `radio`, `toggle`, `checkbox`, `date` |
| Action | `button` (`set` changes state locally, `send` messages the agent), `form` (sends the values its inputs bind) |
| Repeat | `each` (`items` expression, `as` name, one `child`; adds `index`) |

Images accept only `data:image/...` URLs and same-origin paths, so a
widget cannot load a remote tracking pixel.

### Expressions

Numbers, strings, `true` / `false` / `null`, lists; names from `state`,
`computed` and `each`; `.key` and `[i]`; `+ - * / % **`, comparisons,
`and` / `or` / `not`, `a ? b : c`. Functions: `round floor ceil abs
min max sum avg len pow sqrt clamp fmt pct range join keys values str
num`. Limits: 2 KB per expression, 10 000 evaluation steps, ranges of
at most 1000 items.

The same language is implemented in Python (validation and the text
fallback) and in the frontend; both pass one shared conformance file,
`tests/fixtures/kt_ui_expressions.json`.

### Messages back to the agent

A `send` on a button or form produces a widget message:

- if the `show_ui` call is waiting (`wait_for: ["result"]`), the call
  returns `message result: {...}`;
- otherwise the message reaches the agent later as an ordinary
  `widget_event` trigger.

### Validation and fallback

The backend validates every spec before it is shown. An invalid spec
returns all problems with their JSON paths, for example
`root.children[2].bind: 'ghost' is not a key of 'state'`, so the model
can fix it in one more call. Every widget also carries a plain-text
rendering of its initial state, shown by clients that cannot draw
`kt.ui`.
