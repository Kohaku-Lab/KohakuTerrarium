import { describe, expect, it, vi } from "vitest"

import { createChatScrollScheduler } from "./chatScrollScheduler"

describe("ChatPanel scroll scheduling", () => {
  function setupScheduler({ nearBottom = true } = {}) {
    const frames = new Map()
    let nextFrame = 1
    const scroll = vi.fn()
    const scheduler = createChatScrollScheduler({
      afterDomCommit: (callback) => callback(),
      requestFrame: (callback) => {
        const id = nextFrame++
        frames.set(id, callback)
        return id
      },
      cancelFrame: (id) => frames.delete(id),
      shouldScroll: () => nearBottom,
      scroll,
    })
    return {
      frames,
      scroll,
      scheduler,
      runFrame() {
        const [[id, callback]] = frames
        frames.delete(id)
        callback()
      },
    }
  }

  it("coalesces repeated requests into one scroll per frame", () => {
    const { frames, scroll, scheduler, runFrame } = setupScheduler()

    scheduler.schedule()
    scheduler.schedule()
    scheduler.schedule()

    expect(frames.size).toBe(1)
    runFrame()
    expect(scroll).toHaveBeenCalledOnce()
  })

  it("upgrades a pending normal request when a force request arrives", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule()
    scheduler.schedule(true)
    runFrame()

    expect(scroll).toHaveBeenCalledOnce()
  })

  it("does not scroll for a normal request after leaving the bottom", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule()
    runFrame()

    expect(scroll).not.toHaveBeenCalled()
  })

  it("cancels the pending frame when disposed", () => {
    const { frames, scroll, scheduler } = setupScheduler()

    scheduler.schedule()
    scheduler.dispose()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()
  })

  it("does not run a forced frame after its scope is invalidated", () => {
    const { frames, scroll, scheduler } = setupScheduler({ nearBottom: false })

    scheduler.schedule(true, "instance:A")
    scheduler.invalidate()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()
  })

  it("does not create a frame from an invalidated DOM commit", () => {
    const commits = []
    const frames = new Map()
    const scheduler = createChatScrollScheduler({
      afterDomCommit: (callback) => commits.push(callback),
      requestFrame: (callback) => {
        frames.set(1, callback)
        return 1
      },
      cancelFrame: (id) => frames.delete(id),
      shouldScroll: () => true,
      scroll: vi.fn(),
    })

    scheduler.schedule(true, "instance:A")
    scheduler.invalidate()
    commits[0]()

    expect(frames.size).toBe(0)
  })

  it("does not merge force state across scopes", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule(true, "instance:A")
    scheduler.schedule(false, "instance:B")
    runFrame()

    expect(scroll).not.toHaveBeenCalled()
  })

  it("suppresses a pending forced scroll until follow mode resumes", () => {
    const { frames, scroll, scheduler } = setupScheduler({ nearBottom: true })

    scheduler.schedule(true, "instance:A")
    scheduler.suppress()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()

    scheduler.resume()
    scheduler.schedule(false, "instance:A")
    expect(frames.size).toBe(1)
  })
})
