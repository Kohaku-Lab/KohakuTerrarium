import { describe, expect, it } from "vitest"

import {
  actorLabel,
  availabilityDisplay,
  isAttention,
  parseActor,
  relativeTime,
  statusDisplay,
} from "./driveStatus"

describe("driveStatus helpers", () => {
  it("statusDisplay maps known + unknown statuses", () => {
    expect(statusDisplay("active")).toMatchObject({ label: "Active", tone: "good" })
    expect(statusDisplay("blocked")).toMatchObject({ label: "Blocked", tone: "bad" })
    expect(statusDisplay("paused")).toMatchObject({ label: "Paused", tone: "warn" })
    const unknown = statusDisplay("nonsense")
    expect(unknown.label).toBe("nonsense")
    expect(unknown.tone).toBe("neutral")
  })

  it("availabilityDisplay is null only when available", () => {
    expect(availabilityDisplay("available")).toBeNull()
    expect(availabilityDisplay("registration_disabled")).toMatchObject({ tone: "bad" })
    expect(availabilityDisplay("registration_incompatible")).toMatchObject({ tone: "bad" })
  })

  it("isAttention flags blocked/failed/orphaned/unavailable/dead-letter", () => {
    expect(isAttention({ status: "active" })).toBe(false)
    expect(isAttention({ status: "blocked" })).toBe(true)
    expect(isAttention({ status: "failed" })).toBe(true)
    expect(isAttention({ status: "active", assignment_state: "orphaned" })).toBe(true)
    expect(isAttention({ status: "active", availability: "registration_disabled" })).toBe(true)
    expect(isAttention({ status: "active" }, { deadLetter: true })).toBe(true)
  })

  it("parseActor + actorLabel split the kind:identity form", () => {
    expect(parseActor("user:alice")).toEqual({ kind: "user", identity: "alice" })
    expect(parseActor("service:pkg/name")).toEqual({ kind: "service", identity: "pkg/name" })
    expect(parseActor("bare")).toEqual({ kind: "", identity: "bare" })
    expect(actorLabel("creature:root")).toBe("root")
    expect(actorLabel(null)).toBe("—")
  })

  it("relativeTime renders past + future coarse buckets", () => {
    expect(relativeTime("")).toBe("")
    const past = new Date(Date.now() - 5 * 60000).toISOString()
    expect(relativeTime(past)).toBe("5m ago")
    const future = new Date(Date.now() + 2 * 3600000).toISOString()
    expect(relativeTime(future)).toBe("in 2h")
  })
})
