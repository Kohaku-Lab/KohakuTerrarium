import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({ runtimeGraphAPI: { snapshot: vi.fn() } }))
vi.mock("@/utils/wsUrl", () => ({ wsUrl: (path) => `ws://test${path}` }))

import { runtimeGraphAPI } from "@/utils/api"
import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"
import { channelNodeId } from "@/utils/graph/data/model"

import { useGraphLiveStore } from "./live"

class FakeSocket {
  static instances = []
  constructor(url) {
    this.url = url
    this.closed = false
    FakeSocket.instances.push(this)
  }
  close() {
    this.closed = true
    this.onclose?.()
  }
}

const snapshot = {
  version: 1,
  graphs: [
    {
      graph_id: "g1",
      members: [{ node_id: "w1", graph_id: "g1_w1" }],
      creatures: [
        {
          creature_id: "a",
          name: "a",
          running: true,
          send_channels: ["tasks"],
          listen_channels: [],
        },
      ],
      channels: [{ name: "tasks" }],
      output_edges: [],
    },
  ],
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function changeRuntime(change) {
  if (change === "user") useAuthStore().sameOriginUser = { id: "new-user" }
  else useHostsStore().activeHostId = "new-host"
  if (change === "host round trip") useHostsStore().activeHostId = null
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  FakeSocket.instances = []
  vi.stubGlobal("WebSocket", FakeSocket)
  vi.useFakeTimers()
  runtimeGraphAPI.snapshot.mockReset()
  runtimeGraphAPI.snapshot.mockResolvedValue(snapshot)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe("graph live store", () => {
  it("opens one socket for many surfaces and closes it with the last release", async () => {
    const live = useGraphLiveStore()
    live.acquire()
    live.acquire()
    expect(FakeSocket.instances).toHaveLength(1)
    expect(FakeSocket.instances[0].url).toBe("ws://test/ws/runtime/graph")
    await vi.runOnlyPendingTimersAsync()
    expect(live.model.creatures.map((c) => c.id)).toEqual(["a"])
    live.release()
    expect(FakeSocket.instances[0].closed).toBe(false)
    live.release()
    expect(FakeSocket.instances[0].closed).toBe(true)
    expect(live.wsStatus).toBe("closed")
  })

  it("reconnects with backoff while a surface still holds the feed", async () => {
    const live = useGraphLiveStore()
    live.acquire()
    const first = FakeSocket.instances[0]
    first.onopen()
    expect(live.wsStatus).toBe("open")
    first.onclose()
    expect(live.wsStatus).toBe("reconnecting")
    await vi.advanceTimersByTimeAsync(600)
    expect(FakeSocket.instances).toHaveLength(2)
    live.release()
  })

  it("records channel messages against the owning session, even from a cluster member graph", async () => {
    const live = useGraphLiveStore()
    live.handleEvent({ type: "snapshot", snapshot })
    live.handleEvent({
      type: "channel_message",
      graph_id: "g1_w1",
      channel: "tasks",
      sender: "a",
      content_preview: "hello",
    })
    const id = channelNodeId("g1", "tasks")
    expect(live.lastMessages[id]).toMatchObject({ sender: "a", preview: "hello", received: 1 })
    expect(live.pulses[id]).toBeGreaterThan(0)
    live.handleEvent({
      type: "channel_message",
      graph_id: "g1",
      channel: "tasks",
      sender: "b",
      content: "second",
    })
    expect(live.lastMessages[id]).toMatchObject({ sender: "b", preview: "second", received: 2 })
  })

  it("coalesces a burst of topology events into one snapshot read", async () => {
    const live = useGraphLiveStore()
    for (const type of [
      "topology_changed",
      "output_wire_added",
      "creature_added",
      "parent_link_changed",
    ])
      live.handleEvent({ type })
    live.handleEvent({ type: "drive_created" })
    expect(runtimeGraphAPI.snapshot).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(200)
    expect(runtimeGraphAPI.snapshot).toHaveBeenCalledTimes(1)
  })

  it("keeps the same snapshot object when a poll returns identical content", async () => {
    const live = useGraphLiveStore()
    await live.refresh()
    const first = live.snapshot
    runtimeGraphAPI.snapshot.mockResolvedValueOnce(JSON.parse(JSON.stringify(snapshot)))
    await live.refresh()
    expect(live.snapshot).toBe(first)
    runtimeGraphAPI.snapshot.mockResolvedValueOnce({ ...snapshot, version: 2 })
    await live.refresh()
    expect(live.snapshot).not.toBe(first)
  })

  it.each(["host", "user", "host round trip"])(
    "does not publish a pending snapshot after a %s change",
    async (change) => {
      const live = useGraphLiveStore()
      const pending = deferred()
      runtimeGraphAPI.snapshot.mockReturnValueOnce(pending.promise)
      const request = live.refresh()
      changeRuntime(change)
      const current = { ...snapshot, version: 2 }
      live.handleEvent({ type: "snapshot", snapshot: current })
      live.error = "current runtime error"
      pending.resolve({ ...snapshot, version: 3 })
      await request
      expect(live.snapshot).toEqual(current)
      expect(live.error).toBe("current runtime error")
    },
  )

  it.each(["host", "user", "host round trip"])(
    "starts an independent refresh after a %s change and retains its pending state",
    async (change) => {
      const live = useGraphLiveStore()
      const old = deferred()
      const current = deferred()
      runtimeGraphAPI.snapshot.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
      const previous = live.refresh()
      changeRuntime(change)
      const request = live.refresh()
      const shared = live.refresh()
      expect(runtimeGraphAPI.snapshot).toHaveBeenCalledTimes(2)
      old.reject(new Error("old runtime failed"))
      await previous
      expect(live.error).toBe("")
      expect(live.loading).toBe(true)
      const coalesced = live.refresh()
      expect(runtimeGraphAPI.snapshot).toHaveBeenCalledTimes(2)
      current.resolve({ ...snapshot, version: 2 })
      await Promise.all([request, shared, coalesced])
      expect(live.snapshot.version).toBe(2)
      expect(live.error).toBe("")
      expect(live.loading).toBe(false)
    },
  )

  it("clears old graph metadata on runtime change and permits identical new content to publish", async () => {
    const live = useGraphLiveStore()
    await live.refresh()
    live.handleEvent({
      type: "channel_message",
      graph_id: "g1",
      channel: "tasks",
      content: "old message",
    })
    changeRuntime("host")
    expect(live.snapshot).toBeNull()
    expect(live.error).toBe("")
    expect(live.loading).toBe(false)
    expect(live.lastMessages).toEqual({})
    expect(live.pulses).toEqual({})
    await live.refresh()
    expect(live.model.creatures.map((c) => c.id)).toEqual(["a"])
  })

  it("keeps the previous snapshot and reports the error when a read fails", async () => {
    const live = useGraphLiveStore()
    await live.refresh()
    runtimeGraphAPI.snapshot.mockRejectedValueOnce(new Error("down"))
    await live.refresh()
    expect(live.error).toBe("down")
    expect(live.model.creatures).toHaveLength(1)
  })
})
