import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { createVisibilityInterval } from "./useVisibilityInterval"

function setVisibility(value) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  })
  document.dispatchEvent(new Event("visibilitychange"))
}

describe("createVisibilityInterval", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setVisibility("visible")
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("does not run an immediate tick while hidden and catches up on visibility", () => {
    const callback = vi.fn()
    setVisibility("hidden")
    const poller = createVisibilityInterval(callback, 5000, { immediate: true })

    poller.start()
    vi.advanceTimersByTime(15000)

    expect(callback).not.toHaveBeenCalled()

    setVisibility("visible")

    expect(callback).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(2)

    poller.stop()
  })

  it("pauses an armed interval while hidden", () => {
    const callback = vi.fn()
    const poller = createVisibilityInterval(callback, 5000)

    poller.start()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(1)

    setVisibility("hidden")
    vi.advanceTimersByTime(15000)
    expect(callback).toHaveBeenCalledTimes(1)

    setVisibility("visible")
    expect(callback).toHaveBeenCalledTimes(2)

    poller.stop()
  })
})
