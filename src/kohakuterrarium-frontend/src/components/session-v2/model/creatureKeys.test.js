import { describe, expect, it } from "vitest"

import { creatureNameFor, tabKeyFor, tabLabel } from "./creatureKeys"

describe("creature keys", () => {
  it("maps the privileged node's real name to the root tab and back", () => {
    expect(tabKeyFor("coordinator", "coordinator")).toBe("root")
    expect(tabKeyFor("worker", "coordinator")).toBe("worker")
    expect(tabKeyFor("worker", null)).toBe("worker")
    expect(creatureNameFor("root", "coordinator")).toBe("coordinator")
    expect(creatureNameFor("root", null)).toBe("root")
    expect(creatureNameFor("worker", "coordinator")).toBe("worker")
  })

  it("labels tabs by real name or #channel, never 'root'", () => {
    expect(tabLabel("root", "coordinator")).toBe("coordinator")
    expect(tabLabel("ch:tasks", "coordinator")).toBe("#tasks")
    expect(tabLabel("swe", null)).toBe("swe")
    expect(tabLabel(null, null)).toBe("")
  })
})
