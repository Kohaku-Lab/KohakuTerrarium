import { flushPromises } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

import { CHAT_AUTO_EXPAND_TOP_PX, createChatHistoryExpander } from "./chatHistoryExpand"

describe("chat history auto expansion", () => {
  function createIdleHarness() {
    const scheduled = []
    const cancelled = new Set()
    return {
      scheduled,
      cancelled,
      scheduleIdle: (callback) => {
        const id = scheduled.length + 1
        scheduled.push({ id, callback })
        return id
      },
      cancelIdle: (id) => cancelled.add(id),
      pendingCount() {
        return scheduled.filter((entry) => !cancelled.has(entry.id)).length
      },
      runNext() {
        const entry = scheduled.find((candidate) => !cancelled.has(candidate.id))
        if (!entry) return false
        scheduled.splice(scheduled.indexOf(entry), 1)
        entry.callback()
        return true
      },
    }
  }

  // A message wrapper whose viewport-relative top is readable and
  // mutable, simulating content prepended above it.
  function createWrapper(top, { connected = true } = {}) {
    const element = {
      isConnected: connected,
      getBoundingClientRect: () => ({ top: element.top, bottom: element.top + 50 }),
    }
    element.top = top
    return element
  }

  function createViewport({ scrollTop = 0, wrappers = [] } = {}) {
    const el = { scrollTop }
    el.getBoundingClientRect = () => ({ top: 0, bottom: 0 })
    el.querySelectorAll = () => wrappers
    return el
  }

  function createExpander(overrides = {}) {
    const idle = overrides.idle || createIdleHarness()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand: vi.fn(),
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
      ...overrides,
    })
    return { idle, expander }
  }

  it("expands one step at the top and compensates the reading position", async () => {
    const wrapper = createWrapper(100)
    const el = createViewport({ scrollTop: 10, wrappers: [wrapper] })
    const expand = vi.fn(() => {
      // Prepending content pushes the anchor message down.
      wrapper.top = 300
    })
    const { expander } = createExpander({ expand, getViewportEl: () => el })

    expect(expander.maybeExpandAtTop(10)).toBe(true)
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(el.scrollTop).toBe(210)
  })

  it("does not double-compensate when the browser already restored the position", async () => {
    const wrapper = createWrapper(100)
    const el = createViewport({ scrollTop: 10, wrappers: [wrapper] })
    // Native scroll anchoring keeps the anchor visually stable while the
    // content above it grows; the anchor delta must then read zero and
    // scrollTop must not be shifted a second time.
    const expand = vi.fn()
    const { expander } = createExpander({ expand, getViewportEl: () => el })

    expander.maybeExpandAtTop(10)
    await flushPromises()

    expect(el.scrollTop).toBe(10)
  })

  it("still expands exactly at the threshold", () => {
    const expand = vi.fn()
    const { expander } = createExpander({ expand })

    expect(expander.maybeExpandAtTop(CHAT_AUTO_EXPAND_TOP_PX)).toBe(true)
    expect(expand).toHaveBeenCalledOnce()
  })

  it("does not expand while the viewport is away from the top", () => {
    const expand = vi.fn()
    const { expander } = createExpander({ expand })

    expect(expander.maybeExpandAtTop(CHAT_AUTO_EXPAND_TOP_PX + 1)).toBe(false)
    expect(expand).not.toHaveBeenCalled()
  })

  it("does not expand when the window has nothing earlier to show", () => {
    const expand = vi.fn()
    const { expander } = createExpander({ expand, canExpand: () => false })

    expect(expander.maybeExpandAtTop(0)).toBe(false)
    expect(expand).not.toHaveBeenCalled()
  })

  it("does not stack expansions while one is in flight", () => {
    const expand = vi.fn()
    const { expander } = createExpander({ expand })

    expect(expander.maybeExpandAtTop(0)).toBe(true)
    expect(expander.maybeExpandAtTop(0)).toBe(false)
    expect(expand).toHaveBeenCalledOnce()
  })

  it("pre-mounts one idle lookahead and never chains further", async () => {
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand })

    expander.maybeExpandAtTop(0)
    await flushPromises()
    expect(idle.scheduled).toHaveLength(1)

    expect(idle.runNext()).toBe(true)
    await flushPromises()
    expect(expand).toHaveBeenCalledTimes(2)
    // runNext consumes the fired entry; a chained schedule would push a
    // new one, so an empty list proves the lookahead never chains.
    expect(idle.scheduled).toHaveLength(0)
  })

  it("drops a pending idle lookahead once the window has nothing earlier", async () => {
    let expandable = true
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand, canExpand: () => expandable })

    expander.maybeExpandAtTop(0)
    await flushPromises()
    expandable = false

    expect(idle.runNext()).toBe(true)
    await flushPromises()
    expect(expand).toHaveBeenCalledOnce()
  })

  it("cancels a pending idle lookahead on demand", async () => {
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand })

    expander.maybeExpandAtTop(0)
    await flushPromises()
    expect(idle.scheduled).toHaveLength(1)

    expander.cancelIdleExpand()
    expect(idle.cancelled).toContain(idle.scheduled[0].id)
    expect(idle.runNext()).toBe(false)
    await flushPromises()
    expect(expand).toHaveBeenCalledOnce()
  })

  it("skips the scroll compensation when the scope changed mid-expansion", async () => {
    const wrapper = createWrapper(100)
    const el = createViewport({ scrollTop: 10, wrappers: [wrapper] })
    let context = "tab"
    const expand = vi.fn(() => {
      wrapper.top = 300
      context = "other-tab"
    })
    const { expander } = createExpander({
      expand,
      getViewportEl: () => el,
      getContext: () => context,
    })

    expander.maybeExpandAtTop(0)
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(el.scrollTop).toBe(10)
  })

  it("ignores a detached anchor element", async () => {
    const wrapper = createWrapper(100, { connected: false })
    const el = createViewport({ scrollTop: 10, wrappers: [wrapper] })
    const expand = vi.fn(() => {
      wrapper.top = 300
    })
    const { expander } = createExpander({ expand, getViewportEl: () => el })

    expander.maybeExpandAtTop(0)
    await flushPromises()

    expect(el.scrollTop).toBe(10)
  })

  it("supersedes a stale lookahead when a new scroll-triggered expansion starts", async () => {
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand })

    expander.maybeExpandAtTop(0)
    await flushPromises()
    expect(idle.pendingCount()).toBe(1)

    // The user reaches the top again before the lookahead fires. The
    // stale handle must be cancelled, not left to fire an extra
    // expansion on top of the fresh one.
    expect(expander.maybeExpandAtTop(0)).toBe(true)
    expect(idle.cancelled).toContain(idle.scheduled[0].id)
    await flushPromises()

    expect(expand).toHaveBeenCalledTimes(2)
    expect(idle.pendingCount()).toBe(1)

    // The replacement lookahead fires once and never chains.
    expect(idle.runNext()).toBe(true)
    await flushPromises()
    expect(expand).toHaveBeenCalledTimes(3)
    expect(idle.pendingCount()).toBe(0)
  })

  it("does not rearm idle work for a new scope after a mid-expansion switch", async () => {
    let context = "tab-a"
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand, getContext: () => context })

    expander.maybeExpandAtTop(0)
    // Scope flips while the expansion is still awaiting its DOM commit.
    context = "tab-b"
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(idle.scheduled).toHaveLength(0)
  })

  it("does not rearm or expand anything after dispose", async () => {
    const expand = vi.fn()
    const { idle, expander } = createExpander({ expand })

    expander.maybeExpandAtTop(0)
    // Unmount lands while the expansion is still in flight, so the
    // continuation (not just a pending handle) must be fenced off.
    expander.dispose()
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(idle.scheduled).toHaveLength(0)
    expect(expander.maybeExpandAtTop(0)).toBe(false)
    expect(expander.scheduleIdleExpand()).toBeUndefined()
    expect(idle.scheduled).toHaveLength(0)
  })

  it("warns instead of rejecting when the expand callback throws", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const expand = vi.fn(() => {
      throw new Error("boom")
    })
    const { expander } = createExpander({ expand })

    expect(expander.maybeExpandAtTop(0)).toBe(true)
    await flushPromises()

    expect(warn).toHaveBeenCalledOnce()
    expect(expander.maybeExpandAtTop(0)).toBe(true)
    warn.mockRestore()
  })
})
