/**
 * Mermaid diagrams for chat: loaded on first use, rendered with
 * ``securityLevel: "strict"`` in the page's light or dark theme, and cached
 * by theme and source so a re-rendered message reuses its SVG.
 *
 * Host-neutral: callers pass the page's ``Document`` (``pageTheme``,
 * ``renderMermaid``) instead of this module reading a global.
 */

const MAX_CACHE = 100

let loading = null
let seq = 0
let styleNonce = ""
const svgs = new Map()
const failures = new Map()

/** ``"dark"`` or ``"default"``: the Mermaid theme matching ``doc``'s color mode. */
export function pageTheme(doc) {
  return doc?.documentElement?.classList.contains("dark") ? "dark" : "default"
}

/** Mark the ``<style>`` inside each SVG with ``nonce`` (a host whose CSP allows styles by nonce). */
export function setMermaidStyleNonce(nonce) {
  styleNonce = typeof nonce === "string" && /^[A-Za-z0-9+/=_-]+$/.test(nonce) ? nonce : ""
}

function keyOf(source, theme) {
  return `${theme}\n${source}`
}

function remember(map, key, value) {
  map.delete(key)
  map.set(key, value)
  if (map.size > MAX_CACHE) map.delete(map.keys().next().value)
}

function loadMermaid() {
  loading ??= import("mermaid").then((mod) => mod.default || mod)
  return loading
}

/** The cached SVG for ``source`` in ``theme``, or null. */
export function cachedMermaid(source, theme = "default") {
  return svgs.get(keyOf(source, theme)) ?? null
}

/** The cached render error for ``source`` in ``theme``, or null. */
export function mermaidFailure(source, theme = "default") {
  return failures.get(keyOf(source, theme)) ?? null
}

/** Render ``source`` to an SVG string in ``doc``'s theme; rejects with the Mermaid error. */
export async function renderMermaid(source, doc = null) {
  const theme = pageTheme(doc)
  const key = keyOf(source, theme)
  if (svgs.has(key)) return svgs.get(key)
  if (failures.has(key)) throw new Error(failures.get(key))
  const mermaid = await loadMermaid()
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme })
  const id = `kt-mermaid-${++seq}`
  try {
    const { svg } = await mermaid.render(id, source)
    const marked = styleNonce
      ? svg.replace(/<style(?=[\s>])/g, `<style nonce="${styleNonce}"`)
      : svg
    remember(svgs, key, marked)
    return marked
  } catch (err) {
    doc?.getElementById(`d${id}`)?.remove()
    doc?.getElementById(id)?.remove()
    const message = err?.message || String(err)
    remember(failures, key, message)
    throw new Error(message)
  }
}

/** Fill every ``.kt-mermaid`` placeholder under ``root`` that has no diagram yet. */
export function hydrateMermaid(root) {
  if (!root?.querySelectorAll) return
  const doc = root.ownerDocument || null
  const theme = pageTheme(doc)
  for (const el of root.querySelectorAll(".kt-mermaid:not([data-state])")) {
    const source = el.getAttribute("data-source") || ""
    const cached = cachedMermaid(source, theme)
    if (cached) {
      el.innerHTML = cached
      el.setAttribute("data-state", "done")
      continue
    }
    el.setAttribute("data-state", "loading")
    renderMermaid(source, doc)
      .then((svg) => {
        if (!el.isConnected) return
        el.innerHTML = svg
        el.setAttribute("data-state", "done")
      })
      .catch(() => {
        if (el.isConnected) el.setAttribute("data-state", "error")
      })
  }
}
