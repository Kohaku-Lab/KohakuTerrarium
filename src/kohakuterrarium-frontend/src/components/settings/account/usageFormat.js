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

export function formatDateTime(epochSeconds, style = "full") {
  const n = finiteNumber(epochSeconds)
  if (n == null) return ""
  const date = new Date(n * 1000)
  if (!Number.isFinite(date.getTime())) return ""
  if (style === "full") return date.toLocaleString()
  const time = { hour: "2-digit", minute: "2-digit" }
  if (style === "updated" && date.toDateString() === new Date().toDateString()) {
    return date.toLocaleTimeString(undefined, time)
  }
  return date.toLocaleString(undefined, { month: "short", day: "numeric", ...time })
}

export function formatCreditExpiry(iso, style = "compact") {
  if (typeof iso !== "string" || !iso.trim()) return ""
  return formatDateTime(Date.parse(iso) / 1000, style)
}
