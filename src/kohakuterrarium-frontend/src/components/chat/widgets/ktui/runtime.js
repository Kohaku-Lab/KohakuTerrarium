/**
 * kt.ui runtime helpers: build the expression scope (state + computed in
 * dependency order), resolve templated props without throwing, and collect
 * the inputs a form binds.
 */

import { ExprError, evaluate, freeNames, interpolate, parse, toDisplay, truthy } from "./expr.js"

export const MAX_EACH_ITEMS = 500

/** Computed names in dependency order; names in a cycle are left out. */
export function computedOrder(computed) {
  const names = Object.keys(computed || {})
  const deps = {}
  for (const name of names) {
    try {
      deps[name] = [...freeNames(parse(computed[name]))].filter((n) => names.includes(n))
    } catch {
      deps[name] = []
    }
  }
  const order = []
  const marks = {}
  const visit = (name) => {
    if (marks[name] === "done") return true
    if (marks[name] === "active") return false
    marks[name] = "active"
    const ok = [...deps[name]].sort().every(visit)
    marks[name] = "done"
    if (ok) order.push(name)
    return ok
  }
  for (const name of names) visit(name)
  return order
}

/** State merged with every computed value; an expression that fails yields null. */
export function buildScope(spec, state, order = computedOrder(spec?.computed)) {
  const scope = { ...(spec?.state || {}), ...(state || {}) }
  const computed = spec?.computed || {}
  for (const name of order) {
    try {
      scope[name] = evaluate(computed[name], scope)
    } catch {
      scope[name] = null
    }
  }
  return scope
}

/** ``{value, error}`` for a prop: templates are resolved, failures reported. */
export function resolve(value, scope) {
  if (typeof value !== "string") return { value, error: "" }
  try {
    return { value: interpolate(value, scope), error: "" }
  } catch (err) {
    return { value: null, error: err instanceof ExprError ? err.message : String(err) }
  }
}

/** Resolve a prop to display text; a failed template shows a warning sign. */
export function resolveText(value, scope) {
  const { value: out, error } = resolve(value, scope)
  return error ? "⚠" : toDisplay(out)
}

/** Resolve templates inside a JSON value (objects and arrays included). */
export function resolveDeep(value, scope) {
  if (typeof value === "string") return resolve(value, scope).value
  if (Array.isArray(value)) return value.map((v) => resolveDeep(v, scope))
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveDeep(v, scope)]))
  }
  return value
}

export function isVisible(node, scope) {
  if (!("visible" in node)) return true
  const { value, error } = resolve(node.visible, scope)
  return !error && truthy(value)
}

/** Items of an ``each`` node (at most ``MAX_EACH_ITEMS``), or an error. */
export function eachItems(node, scope) {
  try {
    const items = evaluate(node.items, scope)
    if (!Array.isArray(items)) return { items: [], error: "items is not a list" }
    return { items: items.slice(0, MAX_EACH_ITEMS), error: "" }
  } catch (err) {
    return { items: [], error: err.message }
  }
}

/** ``bind`` names of every input inside ``node`` (``each`` templates included). */
export function boundNames(node, out = new Set()) {
  if (!node || typeof node !== "object") return out
  if (typeof node.bind === "string") out.add(node.bind)
  for (const child of node.children || []) boundNames(child, out)
  if (node.child) boundNames(node.child, out)
  return out
}

/** A same-origin path or ``data:image/`` URL; anything else is refused. */
export function safeImageSrc(src) {
  if (typeof src !== "string") return ""
  if (src.startsWith("data:image/")) return src
  if (src.startsWith("/") && !src.startsWith("//")) return src
  return ""
}

/** Columns as ``{key, label, align}``. */
export function tableColumns(columns) {
  return (Array.isArray(columns) ? columns : []).map((c, i) =>
    c && typeof c === "object"
      ? { key: c.key, label: c.label ?? String(c.key), align: c.align || "left", index: i }
      : { key: c, label: String(c), align: "left", index: i },
  )
}

/** One table cell: an object row by key, an array row by position. */
export function tableCell(row, column) {
  if (Array.isArray(row)) return row[column.index]
  if (row && typeof row === "object") return row[column.key]
  return column.index === 0 ? row : null
}

/** Options as ``{value, label}``. */
export function normalizeOptions(options) {
  return (Array.isArray(options) ? options : []).map((o) =>
    o && typeof o === "object"
      ? { value: o.value, label: toDisplay(o.label ?? o.value) }
      : { value: o, label: toDisplay(o) },
  )
}
