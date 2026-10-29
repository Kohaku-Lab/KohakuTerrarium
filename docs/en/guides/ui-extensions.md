# UI extensions

Any module can own dashboard UI: a **package**, a **plugin**, a **tool**
or a **trigger**. The UI is inserted into fixed mount points, and every
component talks to **its own owner** through one SDK call, `useOwner()`.
The code of a component is the same whatever kind its owner is.

This guide covers:

1. [Mount points and owners](#mount-points-and-owners)
2. [The `ui:` manifest slot](#the-ui-manifest-slot)
3. [Writing the frontend part](#writing-the-frontend-part)
4. [Writing the owner side](#writing-the-owner-side)
5. [The owner RPC and push protocol](#the-owner-rpc-and-push-protocol)
6. [Loading, assets and security](#loading-assets-and-security)
7. [Developer workflow](#developer-workflow)
8. [Built-in contributions](#built-in-contributions)

## Mount points and owners

| Mount kind (`kind:`) | Where it shows | Owner kind | Scope |
|---|---|---|---|
| `settings` | a section in Settings | `package` | host-wide |
| `tab` | a workspace tab (new-tab menu) | `package` | host-wide |
| `rail` | a group in the left rail | `package` | host-wide |
| `panel` | a dockable chat/session side panel | `plugin` | one creature |
| `tool-renderer` | replaces the body of that tool's call/result block in chat | `tool` | one creature |
| `trigger-renderer` | replaces the body of that trigger's event block in chat | `trigger` | one creature |
| `widget` | draws every chat widget whose renderer id is the contribution's `renderer` | any | wherever the widget opens |

A `panel` is shown only while the **focused creature has that plugin
loaded and enabled**; otherwise it renders nothing. Renderers render past
and live blocks of their tool or trigger. If two packages provide a
renderer for the same tool, the first one registered wins and the other is
reported in the browser console.

A `widget` contribution names the renderer id it provides in `renderer:`
(a dotted id such as `board.grid`; the `kt.` and `mcp.` prefixes are
reserved). Every declared widget renderer is announced to the backend in the
client's `client_hello`, so tools that require it become visible.

Owner references are `<kind>:<name>`:

- `package:<pkg>` (or just `package`) — the package itself. A package may
  not claim another package.
- `plugin:<plugin name>`, `tool:<tool name>`, `trigger:<trigger id>` —
  a module running on a creature. A trigger may also match by its
  `ui_name` class attribute.

## The `ui:` manifest slot

```yaml
# kohaku.yaml
ui:
  sdk: ">=1,<2"                 # required SDK range (default ">=1,<2")
  root: ui                      # folder that is served (default "ui")
  entry: ui/index.js            # ESM module; default export register(kt)
  styles: [ui/style.css]        # optional stylesheets
  i18n:                         # optional message bundles (JSON)
    en: ui/i18n/en.json
  handler: my_pkg.ui:PackageUI  # optional package owner class
  api: my_pkg.api:router        # optional raw-route fallback (APIRouter / FastAPI)
  contributes:
    - kind: settings            # settings | tab | rail | panel | tool-renderer | trigger-renderer
      id: login                 # [a-z0-9][a-z0-9._-]*, unique in the package
      label: "QQ Bot"
      owner: package            # default for settings / tab / rail
      icon: i-carbon-chat       # optional UnoCSS icon class
      description: ""           # optional
      order: 100                # optional sort order
      minDensity: compact       # compact | regular (tabs)
      isolation: inline         # inline | frame (frame is reserved, see below)
    - kind: panel
      id: chats
      label: "QQ chats"
      owner: plugin:qq_feed     # required for panel / renderers
```

Rules (checked without importing package code):

- `root` must resolve (after symlinks) inside the package; `entry`,
  `styles` and `i18n` paths must be inside `root`. Served files are
  checked against both.
- `entry` is required when any contribution is `inline`.
- A `plugin:` / `tool:` / `trigger:` owner must be a module this package's
  own manifest declares in its `plugins:` / `tools:` / `triggers:` slot.
  (Built-in contributions shipped with the dashboard are exempt.)
- `sdk` is a comma-separated list of `>= <= > < = ^ ~` comparators.
- An invalid block marks only that package as broken; the error is shown
  in the Catalog and the package's UI does not load.

`isolation: frame` is accepted and reserved for a future sandboxed-iframe
mode. In SDK v1 the dashboard shows a "not supported yet" placeholder for
it and loads nothing. The SDK already exports the reserved names
(`FRAME_PROTOCOL`, `FRAME_MESSAGES`, `FRAME_SUPPORTED = false`,
`createFrameBridge()` which throws).

## Writing the frontend part

The entry module is plain ESM. Bare imports of `vue`, `element-plus` and
`@kohakuterrarium/ui-sdk` resolve through the page's import map to the
dashboard's own Vue instance and SDK, so no bundler is needed and
`template:` strings work (the template compiler loads on first use).

```js
import { defineExtension, useOwner } from "@kohakuterrarium/ui-sdk"

const Panel = {
  setup() {
    const owner = useOwner()
    return { owner, reset: () => owner.call("reset") }
  },
  template: `<div>Turns: {{ owner.state.value?.turns ?? 0 }}
    <button @click="reset">Reset</button></div>`,
}

export default defineExtension((kt) => {
  kt.registerPanel("chats", Panel)
})
```

### The `kt` handle (passed to `register(kt)`)

| Member | Purpose |
|---|---|
| `kt.package`, `kt.sdkVersion` | identity |
| `registerTab / registerPanel / registerRailGroup / registerSettingsSection / registerToolRenderer / registerTriggerRenderer / registerWidgetRenderer (id, component, options?)` | provide the component for a declared contribution. An undeclared id is added on the fly; runtime-owned kinds then need `options.owner`, and an undeclared widget needs `options.renderer`. `registerWidgetRenderer` also takes `version` and `surfaces` (`chat`, `canvas`, `side-panel`, `modal`). |
| `kt.t(key, params)` | message from the package's i18n bundles (`{name}` interpolation) |
| `kt.assetUrl(path)` | URL of a file under the UI root, with a currently valid token (tokens are renewed before they expire) |
| `kt.api.get/post/put/patch/delete/request` | raw-route fallback, `/api/ext/<pkg>/<path>` on the active host |
| `kt.ws(path)` | WebSocket to `/api/ext/<pkg>/<path>` (raw fallback) |
| `kt.openTab(id)` | open one of the package's tabs |

### SDK exports (`@kohakuterrarium/ui-sdk` v1)

`SDK_VERSION`, `satisfiesRange`, `defineExtension`, `useExtension`,
`useOwner`, `OwnerCallError`, `useHost`, `useSession`, `useCreature`,
`useSessionEvents`, `useLocale`, `useTheme`, `THEME_TOKENS`, `notify`,
`confirm`, `openTab`, `FRAME_PROTOCOL`, `FRAME_MESSAGES`, `FRAME_SUPPORTED`,
`FrameIsolationUnsupportedError`, `createFrameBridge`.

`useOwner()` returns:

| Member | Meaning |
|---|---|
| `state` | ref: the owner's `ui_state()`, replaced by every `"state"` push |
| `status` | ref: `idle` / `loading` / `ready` / `absent` (owner not loaded) / `error` |
| `error` | ref: last error message |
| `call(action, args)` | runs `ui_call`; rejects with `OwnerCallError` (`code`, `status`) |
| `on(event, fn)` | other pushes; `"*"` receives `(event, data)`; removed with the component |
| `refresh()` | re-read `ui_state()` |

For runtime owners the scope is the session the component is mounted in
and its focused creature; it follows the user when the focus changes.

Renderers receive their block as a prop: tool renderers get `call` (the
tool-call record: `name`, `args`, `status`, `result`, …), trigger renderers
get `event` (`triggerName`, `triggerUiName`, `content`, `triggerContent`,
`channel`, `sender`). A trigger renderer matches the trigger id or the
trigger class's `ui_name`, so runtime-added triggers with generated ids
still find their renderer.

Widget renderers get `widget` (`id`, `renderer`, `version`, `title`,
`spec`, `state`, `surface`, `interactive`, `awaiting`, `closed`,
`fallback`) and `host` (`send(name, data)` sends a widget message,
`setState(patch)` saves state, debounced). Nested components reach the same
pair with `useWidget()`.

Colors and sizes come from CSS variables (`THEME_TOKENS`, e.g.
`--color-surface`, `--color-text`, `--kt-fs-body`); they follow dark mode.

## Writing the owner side

Every owner kind implements the same optional interface:

```python
from kohakuterrarium.modules.ui_owner import (
    OwnerArgumentError, OwnerForbiddenError, UIOwnerMixin,
)

class PackageUI(UIOwnerMixin):                     # referenced by ui.handler
    def ui_state(self, context=None) -> dict:       # sync or async
        return {"bound": self.bound}

    def ui_call(self, action, args, context=None):  # sync or async
        if action == "bind":
            if context.multi_user and not context.is_admin:
                raise OwnerForbiddenError("admins only")
            ...
            self.ui_emit("state", self.ui_state())   # push to subscribers
            return {"ok": True}
        raise OwnerArgumentError(f"unknown action {action}")

    def close(self):          # optional; or `async def aclose(self)`
        ...                   # release resources when the handler is replaced
```

- **Packages**: `ui.handler` names a class; one instance per host process
  is created on first use. It is rebuilt only when **that package's own**
  manifest changes (installing another package never touches it); the
  replaced instance's optional `aclose()` / `close()` is called.
- **Plugins, tools, triggers**: implement the methods on the class. The
  live instance on the addressed creature handles the call. `BasePlugin`,
  `BaseTool` and `BaseTrigger` already provide `ui_emit`. A trigger may
  set the class attribute `ui_name` to be addressable as
  `trigger:<ui_name>` whatever its runtime id is.
- `context` is passed to `ui_state` and `ui_call` when the method accepts
  it. It is a `UICallContext` (exported from `kohakuterrarium.api.ext`):
  `user_id`, `role`, `owner`, `creature_id`, `multi_user` (whether the host
  runs multi-user mode) and `is_admin` (admin token and, under multi-user
  mode, the admin role — the same rule as `require_admin`). On a WS
  subscribe, `ui_state(context)` runs once so the owner can refuse.
- **Errors**: raise `OwnerArgumentError` for a bad action or arguments
  (400 `bad_request`) and `OwnerForbiddenError` when the caller may not do
  it (403 `owner_forbidden`, an `error` frame on the socket; not logged as a
  failure). Any other exception is 500 `owner_failed` and is logged with its
  traceback.
- `ui_emit(event, data, user_id=None)` is a no-op until a UI subscribes;
  it may be called from any thread.

### Multi-user hosts (package owners)

A package owner is shared by every user of the host, so on a multi-user
host:

- `ui_state` and `ui_call` **must accept `context`**; otherwise every
  request, call and subscribe is refused with 403 `owner_not_multi_user`.
- A push is delivered only to the user named by `ui_emit(..., user_id=)`.
  An untargeted push reaches subscribers only on single-user hosts.

Runtime owners (plugin / tool / trigger) live in one user's engine, so
their pushes reach every subscriber of that creature.

### Host-wide resources

Some package resources belong to the whole host, not to one user (a chat
bot bound to the host, a hardware device). Code inside a creature asks
whether it may use them:

```python
from kohakuterrarium.terrarium import host_resources_allowed

if not host_resources_allowed(context):  # ToolContext / PluginContext /
    raise PermissionError("...")          # bound trigger / Agent
```

The answer comes from the engine's `host_resources` flag
(`Terrarium(host_resources=...)`, default `True`). A solo agent, or a
source not inside any engine, counts as allowed. On a multi-user host the
API layer sets the flag per user engine: `True` for admin users, `False`
for everyone else and for the anonymous slot, refreshed on each request so
a role change applies to the live engine. Single-user hosts, `kt run` and
the CLI keep `True`.

The built-in `budget` plugin is a working example (`ui_state()` plus a
`"state"` push after every turn and tool call).

## The owner RPC and push protocol

HTTP (normal auth layers apply; under required multi-user mode an
anonymous caller gets 401):

```
POST /api/extensions/owner/state
  {"owner": "plugin:budget", "session_id": "<graph id>", "creature_id": "<id or name>"}
  → {"owner": "plugin:budget", "state": {...}}

POST /api/extensions/owner/call
  {"owner": ..., "session_id": ..., "creature_id": ..., "action": "reset", "args": {}}
  → {"owner": ..., "result": ...}
```

`session_id` / `creature_id` are required for runtime owners and ignored
for `package:` owners. The creature must belong to that session on the
caller's own engine (per-user under multi-user mode), so a runtime owner
cannot be reached across sessions or users. A runtime owner declared by a
package whose UI is disabled is refused as well. Failures return
`{"detail": {"error": <code>, "message": ...}}`:

| Code | Status | Meaning |
|---|---|---|
| `owner_not_loaded` | 404 | no such owner in this scope (also: another session or user) |
| `owner_disabled` | 404 | the declaring package's UI is disabled |
| `owner_has_no_ui` | 404 | the owner implements neither method |
| `owner_forbidden` | 403 | the owner raised `OwnerForbiddenError` |
| `owner_not_multi_user` | 403 | package owner without `context` on a multi-user host |
| `bad_request` | 400 | `OwnerArgumentError`, or a malformed request |
| `owner_unsupported` | 501 | e.g. a creature on another node |
| `owner_failed` | 500 | any other exception from the owner |

Pushes use one dedicated, multiplexed WebSocket per host,
`/ws/extensions/owner`:

```
client → {"op": "subscribe", "id": "s1", "owner": ..., "session_id": ..., "creature_id": ...}
client → {"op": "unsubscribe", "id": "s1"}
client → {"op": "ping"}
server → {"type": "subscribed", "id": "s1", "owner": ...}
server → {"type": "event", "id": "s1", "event": "state", "data": {...}}
server → {"type": "error", "id": "s1", "error": "owner_not_loaded", "message": ...}
server → {"type": "pong"}
server → {"type": "overflow", "dropped": 12}
```

A dedicated socket is used instead of the session chat socket because
package owners have no session, and because one socket then carries every
owner on the page with its own small protocol. The client re-subscribes
after a reconnect.

Each socket queues at most 256 outgoing frames. When a slow client falls
behind, the **oldest** queued frames are dropped and the next frame is
`overflow`; the dashboard then re-reads the state of every owner on that
socket. If the server cannot send at all, the socket session ends and its
subscriptions are removed.

## Loading, assets and security

- `GET /api/extensions/ui` lists every UI package with its contributions,
  `enabled`, `choice`, `enabled_by_default`, `error` and `dev_url`.
- Contributions are registered as soon as the listing arrives, so
  launchers appear at once; the entry, styles and bundles load when a
  contribution first renders.
- Assets are served from `/api/ext/<pkg>/ui/<token>/<path>`. The token is
  short-lived (6 h), signed per process **for one package**, minted by
  `POST /api/extensions/ui/asset-token` `{"package": "<pkg>"}`, and only
  opens GET/HEAD on that package's asset paths (a token for one package is
  refused for another with 403 `asset_token_invalid`). It sits in the path
  so relative imports keep it. The dashboard renews tokens before they
  expire and re-imports an entry once with a fresh token if loading fails.
- The installed-package scan and the enable choices are cached for about
  2 s; installs, uninstalls and enable toggles take effect at once, other
  manifest edits within that window.
- Each contribution renders inside an error boundary: a crash shows an
  error card with Retry and never breaks the dashboard.
- Extension JS runs in the dashboard origin with full access, the same
  trust as the package's Python code.
- **Enable/disable**: Catalog → Installed shows "contributes UI" and a
  switch (`PUT /api/extensions/ui/<pkg>` `{"enabled": true|false|null}`,
  admin-gated). On single-user hosts packages are enabled by default; under
  multi-user mode they stay off until an admin enables them.
- Switching to another host (or user) drops that host's extensions and
  loads the new host's set.
- The raw `ui.api` router is a **fallback** for what the owner RPC cannot
  express (file downloads, streaming). It is served under
  `/api/ext/<pkg>/` with the host's auth and state; when the host requires
  user accounts, an anonymous request gets 401 before reaching the package.
  Use the helpers in `kohakuterrarium.api.ext` (`current_service`,
  `current_engine`, `studio`, `current_user`, `require_user`,
  `require_admin`, `caller_is_admin`, `package_name`) instead of importing
  `api.auth`.
- The Element Plus copy given to extensions has its `:root` / dark-mode
  theme variables removed at build time, so the dashboard's theme wins.

## Developer workflow

```bash
kt extension new my-ext --ui      # settings section + plugin side panel, both with useOwner()
kt install ./my-ext -e
kt serve
```

- Edit `ui/index.js` and reload the page. Python changes are picked up
  when `kohaku.yaml` changes (or on restart).
- With a bundler: `npx vite` in the package and run the dashboard with
  `KT_UI_DEV=my-ext=http://localhost:5174/ui/index.js`. The generated
  `vite.config.js` keeps the shared modules external.
- Tests: `npm install && npm test`. `ui/test/kt-sdk/sdk-mock.js` is a test
  double of the SDK (`createMockHost({ owner: { state, actions } })`,
  `host.emit(event, data)`, `host.registered`, `host.ownerCalls`).

## Built-in contributions

Core modules ship UI through the same records and `register(kt)` entry;
their entry is a lazy chunk of the dashboard itself
(`src/ext/builtin/`). The built-in **Budget** panel (`plugin:budget`)
shows turn / tool-call / walltime usage of the budget plugin.
