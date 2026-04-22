/**
 * Studio UI store — hover target (safe-triangle preserved) + per-frame
 * column width persistence.
 */

import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useStudioUiStore } from "./ui.js"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear?.()
})

describe("studio ui store — columns", () => {
  it("returns defaults for unknown frame", () => {
    const s = useStudioUiStore()
    expect(s.getColumns("creature-editor")).toEqual({ left: 220, right: 320 })
  })

  it("stores and restores column widths per frameId", () => {
    const s = useStudioUiStore()
    s.setColumns("creature-editor", { left: 260, right: 280 })
    expect(s.getColumns("creature-editor")).toEqual({ left: 260, right: 280 })
    expect(s.getColumns("module-editor")).toEqual({ left: 220, right: 320 })
  })
})

describe("studio ui store — hover target", () => {
  it("starts null", () => {
    const s = useStudioUiStore()
    expect(s.hoverTarget).toBeNull()
  })

  it("setHoverTarget sets the target", () => {
    const s = useStudioUiStore()
    s.setHoverTarget({ kind: "tool", name: "read" })
    expect(s.hoverTarget).toEqual({ kind: "tool", name: "read" })
  })

  it("setHoverTarget replaces a previous target", () => {
    const s = useStudioUiStore()
    s.setHoverTarget({ kind: "tool", name: "read" })
    s.setHoverTarget({ kind: "subagent", name: "explore" })
    expect(s.hoverTarget).toEqual({ kind: "subagent", name: "explore" })
  })

  it("clearHoverTarget clears when no destination is registered", () => {
    // Without a destination the safe triangle is pointless — clear immediately.
    const s = useStudioUiStore()
    s.setHoverTarget({ kind: "tool", name: "read" })
    s.clearHoverTarget()
    expect(s.hoverTarget).toBeNull()
  })

  it("clearHoverTarget with {immediate: true} always clears synchronously", () => {
    const s = useStudioUiStore()
    // Even with a destination, immediate should bypass the safe triangle.
    s.registerHoverDestination({
      getBoundingClientRect: () => ({
        left: 800,
        right: 1100,
        top: 100,
        bottom: 700,
        width: 300,
        height: 600,
      }),
    })
    s.setHoverTarget({ kind: "tool", name: "read" })
    s.clearHoverTarget({ immediate: true })
    expect(s.hoverTarget).toBeNull()
  })

  it("clearHoverTarget preserves target while safe-triangle is armed", () => {
    // With a destination registered, clearHoverTarget arms a watcher;
    // the target does NOT clear synchronously — it waits for mouse
    // movement to leave the wedge / destination to be entered.
    const s = useStudioUiStore()
    s.registerHoverDestination({
      getBoundingClientRect: () => ({
        left: 800,
        right: 1100,
        top: 100,
        bottom: 700,
        width: 300,
        height: 600,
      }),
    })
    s.setHoverTarget({ kind: "tool", name: "read" })
    s.clearHoverTarget()
    // Still set — the mousemove listener handles actual clear.
    expect(s.hoverTarget).toEqual({ kind: "tool", name: "read" })
  })

  it("registerHoverDestination(null) removes the destination", () => {
    const s = useStudioUiStore()
    s.registerHoverDestination({
      getBoundingClientRect: () => ({
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        width: 0,
        height: 0,
      }),
    })
    s.registerHoverDestination(null)
    // After clearing destination, clearHoverTarget takes the fast path
    s.setHoverTarget({ kind: "tool", name: "read" })
    s.clearHoverTarget()
    expect(s.hoverTarget).toBeNull()
  })
})
