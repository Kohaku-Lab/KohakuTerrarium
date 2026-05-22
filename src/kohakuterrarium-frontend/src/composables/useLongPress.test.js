import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useLongPress } from "./useLongPress.js"

function fakeEvent(x, y) {
  return { clientX: x, clientY: y }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useLongPress", () => {
  it("fires after the delay", () => {
    const cb = vi.fn()
    const { onPointerDown } = useLongPress(cb, { delay: 500 })
    onPointerDown(fakeEvent(100, 100))
    vi.advanceTimersByTime(499)
    expect(cb).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it("does not fire if released early", () => {
    const cb = vi.fn()
    const { onPointerDown, onPointerUp } = useLongPress(cb, { delay: 500 })
    onPointerDown(fakeEvent(100, 100))
    vi.advanceTimersByTime(300)
    onPointerUp()
    vi.advanceTimersByTime(1000)
    expect(cb).not.toHaveBeenCalled()
  })

  it("cancels on movement beyond threshold", () => {
    const cb = vi.fn()
    const { onPointerDown, onPointerMove } = useLongPress(cb, {
      delay: 500,
      moveThreshold: 8,
    })
    onPointerDown(fakeEvent(100, 100))
    onPointerMove(fakeEvent(110, 110)) // 14px diag — beyond threshold
    vi.advanceTimersByTime(1000)
    expect(cb).not.toHaveBeenCalled()
  })

  it("does not cancel on tiny jitter", () => {
    const cb = vi.fn()
    const { onPointerDown, onPointerMove } = useLongPress(cb, {
      delay: 500,
      moveThreshold: 8,
    })
    onPointerDown(fakeEvent(100, 100))
    onPointerMove(fakeEvent(103, 102)) // ~3.6px — within threshold
    vi.advanceTimersByTime(500)
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it("cancel handler aborts pending fire", () => {
    const cb = vi.fn()
    const { onPointerDown, onPointerCancel } = useLongPress(cb, {
      delay: 500,
    })
    onPointerDown(fakeEvent(0, 0))
    onPointerCancel()
    vi.advanceTimersByTime(1000)
    expect(cb).not.toHaveBeenCalled()
  })

  it("swallows callback exceptions so the gesture stays robust", () => {
    const cb = vi.fn(() => {
      throw new Error("boom")
    })
    const { onPointerDown } = useLongPress(cb, { delay: 100 })
    onPointerDown(fakeEvent(0, 0))
    // Should not propagate the throw — the callback raises but the
    // gesture handler must keep working for subsequent presses.
    expect(() => vi.advanceTimersByTime(100)).not.toThrow()
    expect(cb).toHaveBeenCalledTimes(1)
  })
})
