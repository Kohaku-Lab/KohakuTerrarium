import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useTraceTimelineStore } from "@/stores/traceTimeline"
import { sessionAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  sessionAPI: { getTimeline: vi.fn() },
}))

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe("trace timeline store", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionAPI.getTimeline.mockReset()
  })

  it("ignores a stale response after switching agents", async () => {
    const oldRequest = deferred()
    sessionAPI.getTimeline.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce({
      agent: "bob",
      spans: [{ eid: 2, type: "text", ts: 2, turn: 2 }],
      truncated: false,
    })
    const store = useTraceTimelineStore("trace-timeline-race")

    const firstLoad = store.load("session", "alice")
    await Promise.resolve()
    await store.load("session", "bob")
    oldRequest.resolve({
      agent: "alice",
      spans: [{ eid: 1, type: "text", ts: 1, turn: 1 }],
      truncated: true,
    })
    await firstLoad

    expect(store.agent).toBe("bob")
    expect(store.records.map((record) => record.eid)).toEqual([2])
    expect(store.truncated).toBe(false)
    expect(store.error).toBe("")
    expect(store.loading).toBe(false)
  })

  it("invalidates an in-flight response when cleared", async () => {
    const request = deferred()
    sessionAPI.getTimeline.mockReturnValueOnce(request.promise)
    const store = useTraceTimelineStore("trace-timeline-clear")

    const load = store.load("session", "alice")
    await Promise.resolve()
    store.clear()
    request.resolve({
      agent: "alice",
      spans: [{ eid: 1, type: "text", ts: 1, turn: 1 }],
      truncated: false,
    })
    await load

    expect(store.sessionName).toBe("")
    expect(store.agent).toBe("")
    expect(store.records).toEqual([])
    expect(store.loading).toBe(false)
  })
})
