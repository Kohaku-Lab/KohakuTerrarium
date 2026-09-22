import { afterEach, describe, expect, it, vi } from "vitest"

import {
  barTone,
  compactProductLabel,
  finiteNumber,
  formatDateTime,
  formatCreditExpiry,
  formatPercentLabel,
  periodKind,
  remainingPercent,
} from "./usageFormat"

afterEach(() => vi.useRealTimers())

describe("usageFormat", () => {
  it("uses minute precision for compact dates and keeps dates on older updates", () => {
    const epoch = Date.parse("2026-09-23T00:59:14Z") / 1000
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date(epoch * 1000))
    const options = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    expect(formatDateTime(epoch, "compact")).toBe(
      new Date(epoch * 1000).toLocaleString(undefined, options),
    )
    expect(formatDateTime(epoch, "updated")).toBe(
      new Date(epoch * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
    )
    vi.setSystemTime(new Date((epoch + 86400) * 1000))
    expect(formatDateTime(epoch, "updated")).toBe(
      new Date(epoch * 1000).toLocaleString(undefined, options),
    )
    expect(formatDateTime(null, "compact")).toBe("")
    expect(formatDateTime(Infinity, "updated")).toBe("")
  })

  it("formats ISO credit expiry without fractions, preserving the full timestamp separately", () => {
    const iso = "2026-10-04T05:32:34.160668Z"
    const options = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    expect(formatCreditExpiry(iso)).toBe(new Date(iso).toLocaleString(undefined, options))
    expect(formatCreditExpiry(iso, "full")).toBe(new Date(iso).toLocaleString())
    expect(formatCreditExpiry("2026-10-04T07:32:34+02:00", "full")).toBe(
      new Date("2026-10-04T05:32:34Z").toLocaleString(),
    )
    for (const invalid of [null, "", "not a date", 0]) expect(formatCreditExpiry(invalid)).toBe("")
  })
  it("keeps numeric zero and rejects missing or non-finite values", () => {
    expect(finiteNumber(0)).toBe(0)
    for (const value of ["0", " ", [], [0], {}]) expect(finiteNumber(value)).toBeNull()
    expect(finiteNumber(null)).toBeNull()
    expect(finiteNumber(undefined)).toBeNull()
    expect(finiteNumber("")).toBeNull()
    expect(finiteNumber("nope")).toBeNull()
    expect(finiteNumber(Number.NaN)).toBeNull()
    expect(finiteNumber(Number.POSITIVE_INFINITY)).toBeNull()
    expect(finiteNumber(true)).toBeNull()
    expect(finiteNumber(false)).toBeNull()
  })

  it("derives remaining only from a finite used percent", () => {
    expect(remainingPercent(1)).toBe(99)
    expect(remainingPercent(0)).toBe(100)
    expect(remainingPercent(100)).toBe(0)
    expect(remainingPercent(null)).toBeNull()
    expect(remainingPercent(Number.NaN)).toBeNull()
    expect(remainingPercent(false)).toBeNull()
  })

  it("maps quota colors without treating missing usage as zero", () => {
    expect(barTone(0)).toBe("purple")
    expect(barTone(79.9)).toBe("purple")
    expect(barTone(80)).toBe("amber")
    expect(barTone(94.9)).toBe("amber")
    expect(barTone(95)).toBe("coral")
    expect(barTone(null)).toBeNull()
    expect(barTone(true)).toBeNull()
  })

  it("classifies weekly, monthly, and unknown periods", () => {
    expect(periodKind("weekly")).toBe("weekly")
    expect(periodKind("MONTHLY")).toBe("monthly")
    expect(periodKind("")).toBe("unknown")
    expect(periodKind(null)).toBe("unknown")
    expect(periodKind("rolling")).toBe("unknown")
  })

  it("compacts a raw product name without inventing a tier", () => {
    expect(compactProductLabel("GrokBuild")).toBe("Build")
    expect(compactProductLabel("grok_imagine")).toBe("Imagine")
    expect(compactProductLabel("Build")).toBe("Build")
    expect(compactProductLabel("")).toBe("")
  })

  it("formats a captured epoch in the browser timezone", () => {
    const formatted = formatDateTime(1790081983)
    expect(formatted).toBe(new Date(1790081983 * 1000).toLocaleString())
    expect(formatDateTime(null)).toBe("")
    expect(formatDateTime("later")).toBe("")
    expect(formatDateTime(1e300)).toBe("")
  })

  it("formats a known percent and leaves unknown blank", () => {
    expect(formatPercentLabel(1)).toBe("1")
    expect(formatPercentLabel(0)).toBe("0")
    expect(formatPercentLabel(12.34)).toBe("12")
    expect(formatPercentLabel(null)).toBe("")
    expect(formatPercentLabel(true)).toBe("")
    expect(formatPercentLabel(false)).toBe("")
  })
})
