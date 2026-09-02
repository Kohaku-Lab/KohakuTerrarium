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
      runNext() {
        const entry = scheduled.find((candidate) => !cancelled.has(candidate.id))
        if (!entry) return false
        scheduled.splice(scheduled.indexOf(entry), 1)
        entry.callback()
        return true
      },
    }
  }

  function createViewport({ scrollTop = 0, height = 1000 } = {}) {
    const el = { scrollTop }
    let viewportHeight = height
    Object.defineProperty(el, "scrollHeight", {
      configurable: true,
      get: () => viewportHeight,
      set: (value) => {
        viewportHeight = value
      },
    })
    return el
  }

  it("expands one step at the top and compensates the reading position", async () => {
    const idle = createIdleHarness()
    const el = createViewport({ scrollTop: 10 })
    const expand = vi.fn(() => {
      // Simulate the prepended content growing the scroll area.
      el.scrollHeight = 3000
    })
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => el,
      getContext: () => "tab",
      ...idle,
    })

    expect(expander.maybeExpandAtTop(10)).toBe(true)
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(el.scrollTop).toBe(2010)
  })

  it("still expands exactly at the threshold", () => {
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...createIdleHarness(),
    })

    expect(expander.maybeExpandAtTop(CHAT_AUTO_EXPAND_TOP_PX)).toBe(true)
    expect(expand).toHaveBeenCalledOnce()
  })

  it("does not expand while the viewport is away from the top", () => {
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...createIdleHarness(),
    })

    expect(expander.maybeExpandAtTop(CHAT_AUTO_EXPAND_TOP_PX + 1)).toBe(false)
    expect(expand).not.toHaveBeenCalled()
  })

  it("does not expand when the window has nothing earlier to show", () => {
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => false,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...createIdleHarness(),
    })

    expect(expander.maybeExpandAtTop(0)).toBe(false)
    expect(expand).not.toHaveBeenCalled()
  })

  it("does not stack expansions while one is in flight", () => {
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...createIdleHarness(),
    })

    expect(expander.maybeExpandAtTop(0)).toBe(true)
    expect(expander.maybeExpandAtTop(0)).toBe(false)
    expect(expand).toHaveBeenCalledOnce()
  })

  it("pre-mounts one idle lookahead and never chains further", async () => {
    const idle = createIdleHarness()
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
    })

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
    const idle = createIdleHarness()
    let expandable = true
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => expandable,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
    })

    expander.maybeExpandAtTop(0)
    await flushPromises()
    expandable = false

    expect(idle.runNext()).toBe(true)
    await flushPromises()
    expect(expand).toHaveBeenCalledOnce()
  })

  it("cancels a pending idle lookahead on demand", async () => {
    const idle = createIdleHarness()
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
    })

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
    const idle = createIdleHarness()
    const el = createViewport({ scrollTop: 10 })
    let context = "tab"
    const expand = vi.fn(() => {
      el.scrollHeight = 3000
      context = "other-tab"
    })
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => el,
      getContext: () => context,
      ...idle,
    })

    expander.maybeExpandAtTop(0)
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(el.scrollTop).toBe(10)
  })

  it("does not rearm idle work for a new scope after a mid-expansion switch", async () => {
    const idle = createIdleHarness()
    const el = createViewport()
    let context = "tab-a"
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => el,
      getContext: () => context,
      ...idle,
    })

    expander.maybeExpandAtTop(0)
    // Scope flips while the expansion is still awaiting its DOM commit.
    context = "tab-b"
    await flushPromises()

    expect(expand).toHaveBeenCalledOnce()
    expect(idle.scheduled).toHaveLength(0)
  })

  it("does not rearm or expand anything after dispose", async () => {
    const idle = createIdleHarness()
    const expand = vi.fn()
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
    })

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
    const idle = createIdleHarness()
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const expand = vi.fn(() => {
      throw new Error("boom")
    })
    const expander = createChatHistoryExpander({
      canExpand: () => true,
      expand,
      getViewportEl: () => createViewport(),
      getContext: () => "tab",
      ...idle,
    })

    expect(expander.maybeExpandAtTop(0)).toBe(true)
    await flushPromises()

    expect(warn).toHaveBeenCalledOnce()
    expect(expander.maybeExpandAtTop(0)).toBe(true)
    warn.mockRestore()
  })
})
