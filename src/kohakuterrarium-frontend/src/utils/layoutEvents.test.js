import { afterEach, describe, expect, it } from "vitest"

import { LAYOUT_EVENTS, fireOpenDrives, onLayoutEvent } from "./layoutEvents.js"

const offs = []
afterEach(() => {
  while (offs.length) offs.pop()()
})

describe("layoutEvents — fireOpenDrives reports whether a panel claimed it", () => {
  it("returns false when nobody is listening", () => {
    expect(fireOpenDrives({ sessionId: "g1" })).toBe(false)
  })

  it("returns false when a listener sees the event but does not claim it", () => {
    let seen = null
    offs.push(onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES, (evt) => (seen = evt.detail)))
    expect(fireOpenDrives({ sessionId: "g1" })).toBe(false)
    expect(seen).toEqual({ sessionId: "g1" })
  })

  it("returns true when a listener calls preventDefault", () => {
    offs.push(onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES, (evt) => evt.preventDefault()))
    expect(fireOpenDrives({ sessionId: "g1" })).toBe(true)
  })
})
