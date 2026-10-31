/**
 * Test double of ``@kohakuterrarium/ui-sdk`` for extension unit tests.
 *
 * Exports the same names as the real SDK, backed by the most recently
 * created mock host instead of a running dashboard. It imports only
 * ``vue`` and its sibling files, so a package can alias the SDK to the
 * copy ``kt extension new --ui`` places in ``ui/test/kt-sdk/``.
 */

import { computed, reactive, ref, shallowRef } from "vue"

import {
  FRAME_MESSAGES,
  FRAME_PROTOCOL,
  FRAME_SUPPORTED,
  FrameIsolationUnsupportedError,
  createFrameBridge,
} from "./frameBridge.js"
import { THEME_TOKENS } from "./theme.js"
import { SDK_VERSION, satisfiesRange } from "./version.js"

export {
  FRAME_MESSAGES,
  FRAME_PROTOCOL,
  FRAME_SUPPORTED,
  FrameIsolationUnsupportedError,
  SDK_VERSION,
  THEME_TOKENS,
  createFrameBridge,
  satisfiesRange,
}

let _current = null

export class OwnerCallError extends Error {
  constructor(code, message, status) {
    super(message || code)
    this.name = "OwnerCallError"
    this.code = code
    this.status = status
  }
}

function current() {
  if (!_current) throw new Error("createMockHost() must run before SDK calls in tests")
  return _current
}

function interpolate(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`))
}

/**
 * Create a mock dashboard. ``routes`` maps ``"METHOD /path"`` to a value
 * or ``(request) => value``; unmatched calls reject with status 404.
 * ``owner`` is ``{ state, actions: { name: (args) => result } }``, or
 * ``null`` for an owner that is not loaded; ``host.emit(event, data)``
 * pushes like the backend's ``ui_emit``.
 */
export function createMockHost(options = {}) {
  const pkg = options.package || "test-pkg"
  const state = reactive({
    host: options.host || { id: null, name: "", url: "", sameOrigin: true },
    session: options.session ?? null,
    creature: options.creature ?? null,
    events: options.events || [],
    locale: options.locale || "en",
    dark: options.dark ?? false,
    confirmAnswer: options.confirmAnswer ?? true,
  })
  const registered = {
    tab: new Map(),
    panel: new Map(),
    rail: new Map(),
    settings: new Map(),
    "tool-renderer": new Map(),
    "trigger-renderer": new Map(),
    widget: new Map(),
    overlay: new Map(),
  }
  const overlayVisible = reactive({})
  const requests = []
  const notifications = []
  const openedTabs = []
  const messages = options.messages || {}
  const routes = options.routes || {}

  async function call(method, path, data, config) {
    const clean = `/${String(path || "").replace(/^\/+/, "")}`
    const request = { method, path: clean, data, config }
    requests.push(request)
    const handler = routes[`${method} ${clean}`]
    if (handler === undefined) {
      const error = new Error(`no mock route for ${method} ${clean}`)
      error.response = { status: 404, data: { error: "not_found" } }
      throw error
    }
    const result = typeof handler === "function" ? await handler(request) : handler
    return { status: 200, data: result }
  }

  const provide =
    (kind) =>
    (id, component, opts = {}) =>
      registered[kind].set(id, { component, options: opts })
  const kt = {
    package: pkg,
    sdkVersion: SDK_VERSION,
    registerTab: provide("tab"),
    registerPanel: provide("panel"),
    registerRailGroup: provide("rail"),
    registerSettingsSection: provide("settings"),
    registerToolRenderer: provide("tool-renderer"),
    registerTriggerRenderer: provide("trigger-renderer"),
    registerWidgetRenderer: provide("widget"),
    registerOverlay: provide("overlay"),
    showOverlay: (id) => (overlayVisible[id] = true),
    hideOverlay: (id) => (overlayVisible[id] = false),
    toggleOverlay: (id) => (overlayVisible[id] = !(overlayVisible[id] ?? true)),
    api: {
      request: (config = {}) =>
        call((config.method || "GET").toUpperCase(), config.url, config.data, config),
      get: (path, config) => call("GET", path, undefined, config),
      delete: (path, config) => call("DELETE", path, undefined, config),
      post: (path, data, config) => call("POST", path, data, config),
      put: (path, data, config) => call("PUT", path, data, config),
      patch: (path, data, config) => call("PATCH", path, data, config),
    },
    ws: (path) => ({
      path,
      sent: [],
      send(data) {
        this.sent.push(data)
      },
      close() {},
      addEventListener() {},
      removeEventListener() {},
    }),
    t(key, params) {
      const text = messages[state.locale]?.[key] ?? messages.en?.[key]
      return text == null ? key : interpolate(text, params)
    },
    assetUrl: (path) => `/mock-assets/${pkg}/${String(path).replace(/^\/+/, "")}`,
    openTab: (id) => openedTabs.push(`${pkg}/${id}`),
  }

  const ownerSpec = options.owner === undefined ? { state: {}, actions: {} } : options.owner
  const ownerState = shallowRef(ownerSpec ? ownerSpec.state : null)
  const ownerStatus = ref(ownerSpec ? "ready" : "absent")
  const ownerHandlers = new Map()
  const ownerCalls = []
  const owner = {
    owner: options.ownerRef || `package:${pkg}`,
    state: ownerState,
    status: ownerStatus,
    error: ref(""),
    async refresh() {
      return ownerState.value
    },
    async call(action, args = {}) {
      ownerCalls.push({ action, args })
      const fn = ownerSpec?.actions?.[action]
      if (!ownerSpec) throw new OwnerCallError("owner_not_loaded", "owner is not loaded", 404)
      if (!fn) throw new OwnerCallError("bad_request", `unknown action ${action}`, 400)
      return fn(args)
    },
    on(event, handler) {
      if (!ownerHandlers.has(event)) ownerHandlers.set(event, new Set())
      ownerHandlers.get(event).add(handler)
      return () => ownerHandlers.get(event)?.delete(handler)
    },
  }

  const host = {
    kt,
    state,
    registered,
    requests,
    notifications,
    openedTabs,
    owner,
    ownerCalls,
    widget: options.widget || null,
    overlayId: options.overlayId || "overlay",
    overlayVisible,
    /** Push an owner event, as the backend's ``ui_emit`` would. */
    emit(event, data) {
      if (event === "state") ownerState.value = data
      for (const handler of ownerHandlers.get(event) || []) handler(data)
      for (const handler of ownerHandlers.get("*") || []) handler(event, data)
    },
    /** Run an entry's ``register(kt)`` against this host. */
    async register(entry) {
      const fn = typeof entry === "function" ? entry : entry?.default || entry?.register
      await fn(kt)
      return registered
    },
  }
  _current = host
  return host
}

export function defineExtension(register) {
  return register
}

export function useExtension() {
  return current().kt
}

export function useOwner() {
  return current().owner
}

/** In tests, the ``{widget, host}`` the mock host was created with (``options.widget``). */
export function useWidget() {
  const widget = current().widget
  if (!widget) throw new Error("useWidget() used outside a widget renderer")
  return widget
}

/** In tests, controls the overlay named by ``createMockHost({ overlayId })``. */
export function useOverlay() {
  const host = current()
  const id = host.overlayId
  const bounds = ref({ left: 0, top: 0, width: 320, height: 240 })
  return {
    visible: computed(() => host.overlayVisible[id] ?? true),
    bounds: computed(() => bounds.value),
    show: () => host.kt.showOverlay(id),
    hide: () => host.kt.hideOverlay(id),
    toggle: () => host.kt.toggleOverlay(id),
    move: (left, top) => (bounds.value = { ...bounds.value, left, top }),
    resize: (width, height) => (bounds.value = { ...bounds.value, width, height }),
    startDrag: () => {},
  }
}

export function useHost() {
  const host = current()
  return computed(() => host.state.host)
}

export function useSession() {
  const host = current()
  return computed(() => host.state.session)
}

export function useCreature() {
  const host = current()
  return computed(() => host.state.creature)
}

export function useSessionEvents() {
  const host = current()
  return computed(() => host.state.events)
}

export function useLocale() {
  const host = current()
  return computed(() => host.state.locale)
}

export function useTheme() {
  const host = current()
  return { dark: computed(() => host.state.dark), tokens: THEME_TOKENS }
}

export function notify(message, type = "info") {
  current().notifications.push({ message: String(message), type })
}

export async function confirm(message) {
  const host = current()
  host.notifications.push({ message: String(message), type: "confirm" })
  return host.state.confirmAnswer
}

export function openTab(target) {
  current().openedTabs.push(target)
}
