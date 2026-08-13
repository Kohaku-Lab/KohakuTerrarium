import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { disposeEventStreamStore, useEventStreamStore } from "@/stores/eventStream"
import { sessionAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  sessionAPI: { getEvents: vi.fn() },
}))

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe("event stream store", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionAPI.getEvents.mockReset()
  })

  it("keeps expanded turns in independent stores", async () => {
    sessionAPI.getEvents
      .mockResolvedValueOnce({ events: [{ event_id: 1 }], next_cursor: null })
      .mockResolvedValueOnce({ events: [{ event_id: 2 }], next_cursor: null })
    const first = useEventStreamStore("trace:session:agent:1")
    const second = useEventStreamStore("trace:session:agent:2")

    await first.loadTurn("session", { agent: "agent", turnIndex: 1 })
    await second.loadTurn("session", { agent: "agent", turnIndex: 2 })

    expect(first.events).toEqual([{ event_id: 1 }])
    expect(second.events).toEqual([{ event_id: 2 }])
    disposeEventStreamStore("trace:session:agent:1")
    disposeEventStreamStore("trace:session:agent:2")
  })

  it("ignores a stale request after switching turns", async () => {
    const oldRequest = deferred()
    sessionAPI.getEvents
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValueOnce({ events: [{ event_id: 2 }], next_cursor: null })
    const stream = useEventStreamStore("trace:race")

    const firstLoad = stream.loadTurn("session", { turnIndex: 1 })
    await Promise.resolve()
    await stream.loadTurn("session", { turnIndex: 2 })
    oldRequest.resolve({ events: [{ event_id: 1 }], next_cursor: null })
    await firstLoad

    expect(stream.turnIndex).toBe(2)
    expect(stream.events).toEqual([{ event_id: 2 }])
    expect(stream.loading).toBe(false)
    disposeEventStreamStore("trace:race")
  })
})
