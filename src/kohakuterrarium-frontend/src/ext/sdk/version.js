/**
 * UI SDK version and the version-range check for a package's ``ui.sdk``.
 *
 * Range grammar (matches ``packages/ui_slot.py``): comma-separated
 * comparators, all of which must hold. Each is ``[op]MAJOR[.MINOR[.PATCH]]``
 * with op one of ``>= <= > < = ^ ~`` (none means ``=``).
 */

export const SDK_VERSION = "1.2.0"

const COMPARATOR_RE = /^(>=|<=|>|<|=|\^|~)?(\d+)(?:\.(\d+))?(?:\.(\d+))?$/

function parseVersion(text) {
  const parts = String(text)
    .split(".")
    .map((n) => parseInt(n, 10) || 0)
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0]
}

function compare(a, b) {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1
  }
  return 0
}

function holds(version, comparator) {
  const m = COMPARATOR_RE.exec(comparator.trim())
  if (!m) return null
  const [, op = "=", major, minor, patch] = m
  const target = [Number(major), Number(minor || 0), Number(patch || 0)]
  const c = compare(version, target)
  switch (op) {
    case ">=":
      return c >= 0
    case "<=":
      return c <= 0
    case ">":
      return c > 0
    case "<":
      return c < 0
    case "^":
      return c >= 0 && version[0] === target[0]
    case "~":
      return c >= 0 && version[0] === target[0] && version[1] === target[1]
    default:
      return c === 0
  }
}

/** True when ``version`` satisfies every comparator in ``range``; false for a malformed range. */
export function satisfiesRange(version, range) {
  const comparators = String(range ?? "")
    .split(",")
    .map((s) => s.trim())
  if (!comparators.length || comparators.some((s) => !s)) return false
  const v = parseVersion(version)
  return comparators.every((c) => holds(v, c) === true)
}
