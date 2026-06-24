import { describe, expect, it } from "vitest"

import { shouldSendOnEnter } from "./chatInput.js"

// Minimal KeyboardEvent-shaped stub.
function ev(overrides = {}) {
  return {
    key: "Enter",
    keyCode: 13,
    isComposing: false,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    ...overrides,
  }
}

describe("shouldSendOnEnter", () => {
  it("sends on a plain Enter (desktop)", () => {
    expect(shouldSendOnEnter(ev(), { isCompact: false })).toBe(true)
  })

  it("defaults isCompact to false when no opts given", () => {
    expect(shouldSendOnEnter(ev())).toBe(true)
  })

  it("does NOT send on the touch/compact shell (mobile newline)", () => {
    // The core mobile fix: plain Enter inserts a newline on compact.
    expect(shouldSendOnEnter(ev(), { isCompact: true })).toBe(false)
  })

  it("does NOT send during IME composition", () => {
    expect(shouldSendOnEnter(ev({ isComposing: true }), { isCompact: false })).toBe(false)
    expect(shouldSendOnEnter(ev({ keyCode: 229 }), { isCompact: false })).toBe(false)
  })

  it("does NOT send for Shift/Ctrl/Cmd/Alt + Enter (newline)", () => {
    expect(shouldSendOnEnter(ev({ shiftKey: true }), { isCompact: false })).toBe(false)
    expect(shouldSendOnEnter(ev({ ctrlKey: true }), { isCompact: false })).toBe(false)
    expect(shouldSendOnEnter(ev({ metaKey: true }), { isCompact: false })).toBe(false)
    expect(shouldSendOnEnter(ev({ altKey: true }), { isCompact: false })).toBe(false)
  })

  it("ignores non-Enter keys", () => {
    expect(shouldSendOnEnter(ev({ key: "a", keyCode: 65 }), { isCompact: false })).toBe(false)
  })
})
