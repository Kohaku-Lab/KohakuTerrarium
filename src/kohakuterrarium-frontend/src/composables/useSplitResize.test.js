/**
 * ``useSplitResize`` — shared pointer-drag resize math for chat
 * group splits + the workspace ``LayoutNode``. The composable is
 * pure-ish: containerEl ref, reactive ratio-derived styles, and a
 * pointer-down handler that listens for move/up.
 *
 * The pointer handler is harder to test in jsdom (no real pointer
 * events), but the style + class derivation is deterministic — we
 * verify the math directly. We also pin the *reactivity contract*
 * (the composable must observe ``getNode()`` changes, not freeze on
 * a destructured value) — the regression that bug guards against
 * is the visible "horizontal split renders with height: 50% instead
 * of width: 50%" issue when a leaf becomes a split on the same
 * ChatGroupNode instance.
 */
import { ref } from "vue"
import { describe, expect, it } from "vitest"

import { useSplitResize } from "./useSplitResize.js"

describe("useSplitResize — computed styles", () => {
  it("horizontal split renders width %", () => {
    const node = { direction: "horizontal", ratio: 30 }
    const r = useSplitResize({ getNode: () => node, onChange: () => {} })
    expect(r.firstStyle.value).toEqual({ width: "30%", height: "100%" })
    expect(r.secondStyle.value).toEqual({ width: "70%", height: "100%" })
    expect(r.handleClass.value).toContain("cursor-col-resize")
  })

  it("vertical split renders height %", () => {
    const node = { direction: "vertical", ratio: 70 }
    const r = useSplitResize({ getNode: () => node, onChange: () => {} })
    expect(r.firstStyle.value).toEqual({ height: "70%", width: "100%" })
    expect(r.secondStyle.value).toEqual({ height: "30%", width: "100%" })
    expect(r.handleClass.value).toContain("cursor-row-resize")
  })

  it("defaults to 50% when ratio is missing", () => {
    const node = { direction: "horizontal" }
    const r = useSplitResize({ getNode: () => node, onChange: () => {} })
    expect(r.firstStyle.value.width).toBe("50%")
    expect(r.secondStyle.value.width).toBe("50%")
  })

  it("defaults to 50% when ratio is NaN / non-finite", () => {
    const r = useSplitResize({
      getNode: () => ({ direction: "horizontal", ratio: NaN }),
      onChange: () => {},
    })
    expect(r.firstStyle.value.width).toBe("50%")
  })

  it("REACTIVITY: tracks direction changes when the node ref updates", () => {
    // Regression for the "horizontal split renders height: 50% instead
    // of width: 50%" bug. ``ChatGroupNode`` reuses the same instance
    // across leaf→split transitions; the composable MUST observe the
    // new node via the getter, not freeze on the initial value.
    const nodeRef = ref({ type: "leaf", groupId: "g1" })
    const r = useSplitResize({ getNode: () => nodeRef.value, onChange: () => {} })
    // Leaf has no direction → falls through to the else branch
    // (vertical-style placeholder).
    expect(r.firstStyle.value).toEqual({ height: "50%", width: "100%" })

    // Same composable instance, node changes to a horizontal split —
    // styles MUST flip to width/height (NOT stay vertical).
    nodeRef.value = { type: "split", direction: "horizontal", ratio: 50 }
    expect(r.firstStyle.value).toEqual({ width: "50%", height: "100%" })
    expect(r.secondStyle.value).toEqual({ width: "50%", height: "100%" })
    expect(r.handleClass.value).toContain("cursor-col-resize")

    // And again to vertical.
    nodeRef.value = { type: "split", direction: "vertical", ratio: 40 }
    expect(r.firstStyle.value).toEqual({ height: "40%", width: "100%" })
    expect(r.handleClass.value).toContain("cursor-row-resize")
  })
})

describe("useSplitResize — onPointerDown invocation surface", () => {
  it("returns containerEl ref + dragging ref", () => {
    const r = useSplitResize({
      getNode: () => ({ direction: "horizontal", ratio: 50 }),
      onChange: () => {},
    })
    expect(r.containerEl).toBeDefined()
    expect(r.dragging.value).toBe(false)
  })

  it("guards against missing containerEl on pointer-down (no throw)", () => {
    const r = useSplitResize({
      getNode: () => ({ direction: "horizontal", ratio: 50 }),
      onChange: () => {},
    })
    // containerEl is unmounted (null); calling onPointerDown should
    // be a defensive no-op (not throw) so a stray drag during
    // teardown is harmless.
    expect(() => {
      r.onPointerDown({
        pointerId: 1,
        target: { setPointerCapture() {}, addEventListener() {} },
      })
    }).not.toThrow()
  })
})
