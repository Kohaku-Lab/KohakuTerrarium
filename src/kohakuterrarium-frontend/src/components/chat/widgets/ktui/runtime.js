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

const NUMERIC_FORMATS = new Set(["number", "percent", "currency"])

/** Columns as ``{key, label, align, format, digits, currency}``. */
export function tableColumns(columns) {
  return (Array.isArray(columns) ? columns : []).map((c, i) => {
    if (!c || typeof c !== "object")
      return { key: c, label: String(c), align: "left", format: "text", index: i }
    const format = typeof c.format === "string" ? c.format : "text"
    const align = c.align || (NUMERIC_FORMATS.has(format) ? "right" : "left")
    return {
      key: c.key,
      label: c.label ?? String(c.key),
      align,
      format,
      digits: c.digits,
      currency: c.currency,
      index: i,
    }
  })
}

/** A cell as text in its column's ``format`` (number, percent, currency, date). */
export function formatCell(value, column) {
  const format = column?.format || "text"
  if (format === "date" && typeof value === "string" && value) {
    const date = new Date(value)
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleDateString(undefined, {
          year: "numeric",
          month: "short",
          day: "numeric",
          timeZone: /T/.test(value) ? undefined : "UTC",
        })
  }
  if (!NUMERIC_FORMATS.has(format) || typeof value !== "number" || !Number.isFinite(value))
    return toDisplay(value)
  const fallback = format === "currency" ? 2 : 0
  const digits =
    Number.isInteger(column.digits) && column.digits >= 0 && column.digits <= 10
      ? column.digits
      : fallback
  const options = { minimumFractionDigits: digits, maximumFractionDigits: digits }
  try {
    if (format === "percent")
      return new Intl.NumberFormat(undefined, { ...options, style: "percent" }).format(value)
    if (format === "currency")
      return new Intl.NumberFormat(undefined, {
        ...options,
        style: "currency",
        currency: String(column.currency || "USD").toUpperCase(),
      }).format(value)
    return new Intl.NumberFormat(undefined, options).format(value)
  } catch {
    return toDisplay(value)
  }
}

function sortKey(value) {
  if (typeof value === "number") return [0, value]
  if (value === null || value === undefined || value === "") return [2, ""]
  return [1, toDisplay(value).toLowerCase()]
}

/** ``rows`` ordered by ``column`` (empty cells last), stable; ``dir`` is ``asc`` or ``desc``. */
export function sortRows(rows, column, dir) {
  if (!column) return rows
  const sign = dir === "desc" ? -1 : 1
  return rows
    .map((row, i) => ({ row, i, key: sortKey(tableCell(row, column)) }))
    .sort((a, b) => {
      if (a.key[0] !== b.key[0]) return a.key[0] - b.key[0]
      if (a.key[1] < b.key[1]) return -sign
      if (a.key[1] > b.key[1]) return sign
      return a.i - b.i
    })
    .map((entry) => entry.row)
}

/** ``rows`` whose shown cells contain ``query`` (case-insensitive). */
export function filterRows(rows, columns, query) {
  const needle = String(query || "")
    .trim()
    .toLowerCase()
  if (!needle) return rows
  return rows.filter((row) =>
    columns.some((col) => formatCell(tableCell(row, col), col).toLowerCase().includes(needle)),
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
