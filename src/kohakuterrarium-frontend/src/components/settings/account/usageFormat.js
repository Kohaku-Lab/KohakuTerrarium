/** Shared usage display helpers. Missing values stay unknown — never 0. */

export function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function remainingPercent(used) {
  const n = finiteNumber(used)
  if (n == null) return null
  return Math.min(100, Math.max(0, 100 - n))
}

/** <80 purple, 80–<95 amber, >=95 coral. Unknown usage has no tone. */
export function barTone(used) {
  const n = finiteNumber(used)
  if (n == null) return null
  if (n >= 95) return "coral"
  if (n >= 80) return "amber"
  return "purple"
}

export function barClass(used) {
  const tone = barTone(used)
  if (tone === "coral") return "bg-coral"
  if (tone === "amber") return "bg-amber"
  if (tone === "purple") return "bg-iolite"
  return ""
}

export function clampPercent(value) {
  const n = finiteNumber(value)
  if (n == null) return 0
  return Math.min(100, Math.max(0, n))
}

export function formatPercentLabel(value) {
  const n = finiteNumber(value)
  if (n == null) return ""
  const digits = Math.abs(n) >= 10 || Number.isInteger(n) ? 0 : 1
  return n.toFixed(digits)
}

export function periodKind(period) {
  const value = String(period || "")
    .trim()
    .toLowerCase()
  if (value === "weekly" || value === "monthly") return value
  return "unknown"
}

export function compactProductLabel(name) {
  const raw = String(name || "").trim()
  if (!raw) return ""
  const stripped = raw.replace(/^grok[\s_-]*/i, "")
  const label = stripped || raw
  return label.charAt(0).toUpperCase() + label.slice(1)
}

export function formatDateTime(epochSeconds) {
  const n = finiteNumber(epochSeconds)
  if (n == null) return ""
  const date = new Date(n * 1000)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : ""
}
