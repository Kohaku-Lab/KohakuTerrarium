/**
 * Widget renderer registry: renderer id → component that draws a widget.
 *
 * A widget event names its renderer (``kt.app``, ``kt.ui``, ``pkg.board``…);
 * the chat looks the id up here. Built-in renderers register at start-up,
 * packages through ``kt.registerWidgetRenderer`` or a ``kind: widget``
 * contribution. The first registration of an id wins. ``helloRenderers``
 * is what the client announces in ``client_hello``; the built-in legacy
 * kinds are never announced because the backend does not gate them.
 */

import { markRaw, reactive, watch } from "vue"

export const LEGACY_RENDERER_IDS = Object.freeze([
  "kt.card",
  "kt.ask_text",
  "kt.confirm",
  "kt.selection",
  "kt.progress",
  "kt.notification",
])
const LEGACY = new Set(LEGACY_RENDERER_IDS)

export const WIDGET_SURFACES = Object.freeze(["chat", "canvas", "side-panel", "modal"])
export const WIDGET_TRUST = Object.freeze(["builtin", "package", "sandboxed"])

export const widgetRenderers = reactive(new Map())

/**
 * Register ``component`` as the renderer for ``id``; return whether it took.
 *
 * ``options``: ``version`` (highest spec version, default 1), ``surfaces``
 * (default chat only), ``trust``, ``key`` (owner key used to unregister),
 * ``summary(widget)`` / ``icon(widget)`` for the minimized chrome.
 */
export function registerWidgetRenderer(id, component, options = {}) {
  if (typeof id !== "string" || !id) throw new Error("registerWidgetRenderer needs an id")
  if (!component) throw new Error(`registerWidgetRenderer ${id} needs a component`)
  const key = options.key || `id:${id}`
  const existing = widgetRenderers.get(id)
  if (existing) {
    if (existing.key !== key) {
      console.warn(`[widgets] renderer ${id} already provided by ${existing.key}; ignoring ${key}`)
    }
    return false
  }
  const version = Number.isInteger(options.version) && options.version >= 1 ? options.version : 1
  const surfaces = (options.surfaces || ["chat"]).filter((s) => WIDGET_SURFACES.includes(s))
  widgetRenderers.set(id, {
    id,
    key,
    component: markRaw(component),
    version,
    surfaces: surfaces.length ? surfaces : ["chat"],
    trust: WIDGET_TRUST.includes(options.trust) ? options.trust : "package",
    summary: typeof options.summary === "function" ? options.summary : null,
    icon: typeof options.icon === "function" ? options.icon : null,
  })
  return true
}

/** Remove the renderer for ``id``. */
export function unregisterWidgetRenderer(id) {
  return widgetRenderers.delete(id)
}

/** Remove every renderer registered under a key in ``keys``. */
export function unregisterWidgetRenderersByKey(keys) {
  const drop = new Set(keys)
  for (const [id, entry] of [...widgetRenderers.entries()])
    if (drop.has(entry.key)) widgetRenderers.delete(id)
}

export function findWidgetRenderer(id) {
  return widgetRenderers.get(id) || null
}

/** ``{id: version}`` for ``client_hello``: every renderer except the legacy kinds. */
export function helloRenderers() {
  const out = {}
  for (const [id, entry] of widgetRenderers) if (!LEGACY.has(id)) out[id] = entry.version
  return out
}

/** The surface a renderer actually draws ``surface`` on (falls back to chat). */
export function effectiveSurface(id, surface) {
  const entry = widgetRenderers.get(id)
  if (!entry || !surface) return "chat"
  return entry.surfaces.includes(surface) ? surface : "chat"
}

/**
 * Call ``send(renderers)`` whenever the announced renderer set changes,
 * debounced by ``delayMs``. Returns a stop function.
 */
export function watchHelloRenderers(send, delayMs = 300) {
  let timer = null
  let last = JSON.stringify(helloRenderers())
  const stop = watch(
    () => JSON.stringify(helloRenderers()),
    (next) => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        if (next === last) return
        last = next
        send(JSON.parse(next))
      }, delayMs)
    },
  )
  return () => {
    stop()
    if (timer) clearTimeout(timer)
  }
}
