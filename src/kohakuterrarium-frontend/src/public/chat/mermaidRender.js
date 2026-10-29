/**
 * Mermaid diagrams for chat: loaded on first use, rendered with
 * ``securityLevel: "strict"`` in the page's light or dark theme, and cached
 * by theme and source so a re-rendered message reuses its SVG.
 */

const MAX_CACHE = 100

let loading = null
let seq = 0
const svgs = new Map()
const failures = new Map()

function theme() {
  return globalThis.document?.documentElement?.classList.contains("dark") ? "dark" : "default"
}

function keyOf(source) {
  return `${theme()}\n${source}`
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

/** The cached SVG for ``source`` in the current theme, or null. */
export function cachedMermaid(source) {
  return svgs.get(keyOf(source)) ?? null
}

/** The cached render error for ``source`` in the current theme, or null. */
export function mermaidFailure(source) {
  return failures.get(keyOf(source)) ?? null
}

/** Render ``source`` to an SVG string; rejects with the Mermaid error. */
export async function renderMermaid(source) {
  const key = keyOf(source)
  if (svgs.has(key)) return svgs.get(key)
  if (failures.has(key)) throw new Error(failures.get(key))
  const mermaid = await loadMermaid()
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: theme() })
  const id = `kt-mermaid-${++seq}`
  try {
    const { svg } = await mermaid.render(id, source)
    remember(svgs, key, svg)
    return svg
  } catch (err) {
    globalThis.document?.getElementById(`d${id}`)?.remove()
    globalThis.document?.getElementById(id)?.remove()
    const message = err?.message || String(err)
    remember(failures, key, message)
    throw new Error(message)
  }
}

/** Fill every ``.kt-mermaid`` placeholder under ``root`` that has no diagram yet. */
export function hydrateMermaid(root) {
  if (!root?.querySelectorAll) return
  for (const el of root.querySelectorAll(".kt-mermaid:not([data-state])")) {
    const source = el.getAttribute("data-source") || ""
    const cached = cachedMermaid(source)
    if (cached) {
      el.innerHTML = cached
      el.setAttribute("data-state", "done")
      continue
    }
    el.setAttribute("data-state", "loading")
    renderMermaid(source)
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
