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

  it("skips ticks while the previous async callback is still in flight", async () => {
    let resolveFirst
    const callback = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )
    const poller = createVisibilityInterval(callback, 5000)

    poller.start()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(1)

    // Backend slower than the window: three more ticks land while the
    // first request is unanswered — all must be skipped, not stacked.
    vi.advanceTimersByTime(15000)
    expect(callback).toHaveBeenCalledTimes(1)

    resolveFirst()
    await flushMicrotasks()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(2)

    poller.stop()
  })

  it("resumes polling after an async callback rejects", async () => {
    const callback = vi
      .fn()
      .mockImplementationOnce(() => Promise.reject(new Error("boom")))
      .mockImplementation(() => Promise.resolve())
    const poller = createVisibilityInterval(callback, 5000)

    poller.start()
    vi.advanceTimersByTime(5000)
    await flushMicrotasks()

    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(2)

    poller.stop()
  })

  it("does not let a stale in-flight callback suppress ticks after a restart", async () => {
    let resolveFirst
    const callback = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )
    const poller = createVisibilityInterval(callback, 5000)

    poller.start()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(1)

    poller.stop()
    poller.start()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(2)

    resolveFirst()
    await flushMicrotasks()
    poller.stop()
  })

  it("never skips ticks for a synchronous callback", () => {
    const callback = vi.fn(() => undefined)
    const poller = createVisibilityInterval(callback, 1000)

    poller.start()
    vi.advanceTimersByTime(5000)
    expect(callback).toHaveBeenCalledTimes(5)

    poller.stop()
  })
})

function flushMicrotasks() {
  // Two rounds settle the promise returned by the callback and the
  // settled-handler chained onto it by the interval.
  return Promise.resolve().then(() => Promise.resolve())
}
