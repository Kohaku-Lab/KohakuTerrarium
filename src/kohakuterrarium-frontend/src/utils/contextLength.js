/**
 * Context lengths as users type them (`500000`, `500k`, `1.5m`), as inputs
 * echo them back, and as labels show them, plus the `context=<n>` part of a
 * model selector. A known model window is only ever a hint: nothing here caps
 * a chosen length.
 */

const UNITS = { "": 1, k: 1_000, m: 1_000_000 }

/** Common context lengths offered as quick picks. */
export const CONTEXT_SUGGESTIONS = [200_000, 272_000, 400_000, 1_000_000]

/** `"1.5m"` -> 1500000, `"500k"` -> 500000, `"200,000"` -> 200000; null when blank or not a positive length. */
export function parseContext(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) && value >= 1 ? Math.round(value) : null
  }
  const raw = String(value ?? "")
    .trim()
    .replace(/[,_\s]/g, "")
    .toLowerCase()
  const match = /^(\d+(?:\.\d+)?|\.\d+)([km]?)$/.exec(raw)
  if (!match) return null
  const count = Math.round(Number(match[1]) * UNITS[match[2]])
  return count >= 1 ? count : null
}

/** 1000000 -> "1M", 1500000 -> "1.5M", 272000 -> "272k"; "" for an unknown length. */
export function formatContext(count) {
  const n = parseContext(count)
  if (!n) return ""
  if (n >= 1_000_000) return `${+(n / 1_000_000).toFixed(2)}M`
  if (n >= 1000) return `${+(n / 1000).toFixed(1)}k`
  return String(n)
}

/** The exact short form for an input: 1500000 -> "1.5m", 272000 -> "272k", 131072 -> "131072". */
export function contextText(count) {
  const n = parseContext(count)
  if (!n) return ""
  if (n % 1000) return String(n)
  return n >= 1_000_000 ? `${n / 1_000_000}m` : `${n / 1000}k`
}

/** True when `count` is above a known model `window`; an unknown window never warns. */
export function exceedsWindow(count, window) {
  const n = parseContext(count)
  const limit = parseContext(window)
  return !!n && !!limit && n > limit
}

/** Quick-pick lengths: the common ones plus the model's own window, ascending and unique. */
export function contextSuggestions(window) {
  const limit = parseContext(window)
  const all = limit ? [...CONTEXT_SUGGESTIONS, limit] : CONTEXT_SUGGESTIONS
  return [...new Set(all)].sort((a, b) => a - b)
}

/**
 * Splits a selector's `context=` override from its variation selections:
 * {selections, context}. A preset that has its own `context` variation
 * group keeps that selection as a group choice.
 */
export function takeContext(selections, variationGroups) {
  const rest = { ...(selections || {}) }
  if (variationGroups && "context" in variationGroups) {
    return { selections: rest, context: "" }
  }
  const context = rest.context || ""
  delete rest.context
  return { selections: rest, context }
}

/** `selector` with `context=<n>` appended; unchanged when `context` is blank or not a length. */
export function withContext(selector, context) {
  const n = parseContext(context)
  if (!selector || !n) return selector || ""
  return `${selector}${selector.includes("@") ? "," : "@"}context=${n}`
}
