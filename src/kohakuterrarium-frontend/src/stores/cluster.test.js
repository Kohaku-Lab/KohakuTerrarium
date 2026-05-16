import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const intervalCtl = { start: vi.fn(), stop: vi.fn(), isRunning: vi.fn(() => true) }

vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: vi.fn(() => intervalCtl),
}))

vi.mock("@/utils/api", () => ({
  nodesAPI: {
    list: vi.fn(),
    status: vi.fn(),
    deployCreature: vi.fn(),
  },
}))

import { nodesAPI } from "@/utils/api"
import { siteColorFor, useClusterStore } from "./cluster"

// Build axios-style 404 error so the store's mode-discovery branch
// recognises standalone.
function notFoundError() {
  const e = new Error("Request failed with status code 404")
  e.response = { status: 404, data: { detail: "Node routes are lab-host-only" } }
  return e
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  intervalCtl.start.mockClear()
  intervalCtl.stop.mockClear()
})

afterEach(() => {
  // each test resets the store explicitly via reset()
})

describe("siteColorFor", () => {
  it("returns neutral for host", () => {
    expect(siteColorFor("_host")).toBe("neutral")
  })
  it("returns neutral for empty / falsy", () => {
    expect(siteColorFor("")).toBe("neutral")
    expect(siteColorFor(null)).toBe("neutral")
  })
  it("returns the same color for the same workerId", () => {
    expect(siteColorFor("worker-1")).toBe(siteColorFor("worker-1"))
  })
  it("returns a non-neutral color for a worker id", () => {
    expect(siteColorFor("worker-1")).not.toBe("neutral")
  })
})

describe("cluster store — hydrate", () => {
  it("standalone mode (404 on /api/nodes) → empty sites", async () => {
    nodesAPI.list.mockRejectedValueOnce(notFoundError())
    const store = useClusterStore()
    await store.hydrate()
    expect(store.mode).toBe("standalone")
    expect(store.sites).toEqual([])
    expect(store.isCluster).toBe(false)
  })

  it("lab-host (200 on /api/nodes) → fetches and normalizes site list", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 3 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
      ],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.mode).toBe("lab-host")
    expect(store.isCluster).toBe(true)
    expect(store.siteCount).toBe(2)
    expect(store.showPickers).toBe(true)
    expect(store.sites[0]).toEqual({
      nodeId: "_host",
      isHost: true,
      status: "online",
      creatures: 3,
    })
    expect(store.sites[1].nodeId).toBe("worker-1")
  })

  it("lab-host with only one site → showPickers is false", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.isCluster).toBe(true)
    expect(store.siteCount).toBe(1)
    expect(store.showPickers).toBe(false)
  })

  it("5xx failure → mode unchanged, error recorded, sites kept", async () => {
    // First hydrate succeeds — populates state.
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 1 }],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.mode).toBe("lab-host")
    // Second hydrate fails with 503 — sites stay populated.
    const err = new Error("503")
    err.response = { status: 503 }
    nodesAPI.list.mockRejectedValueOnce(err)
    await store.hydrate()
    expect(store.mode).toBe("lab-host")
    expect(store.sites).toHaveLength(1)
    expect(store.error).toBe("503")
  })

  it("network error with no response → keeps state, records error", async () => {
    nodesAPI.list.mockRejectedValueOnce(new Error("network down"))
    const store = useClusterStore()
    await store.hydrate()
    expect(store.mode).toBe("standalone") // initial state preserved
    expect(store.error).toBe("network down")
  })

  it("normalizes missing fields with safe defaults", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true }],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.sites[0].status).toBe("unknown")
    expect(store.sites[0].creatures).toBe(null)
  })
})

describe("cluster store — getters", () => {
  it("hostSite + workerSites split correctly", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 1 },
        { node_id: "worker-a", is_host: false, status: "online", creatures: 2 },
        { node_id: "worker-b", is_host: false, status: "online", creatures: 0 },
      ],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.hostSite.nodeId).toBe("_host")
    expect(store.workerSites.map((s) => s.nodeId)).toEqual(["worker-a", "worker-b"])
  })

  it("getSite / siteById indexed by nodeId", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 1 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
      ],
    })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.getSite("worker-1").nodeId).toBe("worker-1")
    expect(store.getSite("ghost")).toBe(null)
    expect(store.siteById["_host"].isHost).toBe(true)
  })
})

describe("cluster store — markSiteOffline", () => {
  it("immediately marks the matching site unreachable", async () => {
    nodesAPI.list
      .mockResolvedValueOnce({
        nodes: [
          { node_id: "_host", is_host: true, status: "online", creatures: 1 },
          { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
        ],
      })
      // Second hydrate (triggered by markSiteOffline) sees the worker
      // already gone — still in unreachable status.
      .mockResolvedValueOnce({
        nodes: [
          { node_id: "_host", is_host: true, status: "online", creatures: 1 },
          { node_id: "worker-1", is_host: false, status: "unreachable", creatures: null },
        ],
      })
    const store = useClusterStore()
    await store.hydrate()
    expect(store.getSite("worker-1").status).toBe("online")
    await store.markSiteOffline("worker-1")
    expect(store.getSite("worker-1").status).toBe("unreachable")
  })

  it("is a no-op for an unknown nodeId (still hydrates)", async () => {
    nodesAPI.list
      .mockResolvedValueOnce({
        nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
      })
      .mockResolvedValueOnce({
        nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
      })
    const store = useClusterStore()
    await store.hydrate()
    await store.markSiteOffline("ghost")
    expect(nodesAPI.list).toHaveBeenCalledTimes(2)
    expect(store.siteCount).toBe(1)
  })

  it("is a no-op when nodeId is falsy", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
    })
    const store = useClusterStore()
    await store.hydrate()
    await store.markSiteOffline("")
    expect(nodesAPI.list).toHaveBeenCalledTimes(1)
  })
})

describe("cluster store — polling", () => {
  it("startPolling registers an interval; stopPolling clears it", () => {
    const store = useClusterStore()
    store.startPolling()
    expect(intervalCtl.start).toHaveBeenCalledTimes(1)
    store.stopPolling()
    expect(intervalCtl.stop).toHaveBeenCalledTimes(1)
  })

  it("startPolling is idempotent", () => {
    const store = useClusterStore()
    store.startPolling()
    store.startPolling()
    expect(intervalCtl.start).toHaveBeenCalledTimes(1)
  })

  it("reset stops polling and clears state", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 1 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
      ],
    })
    const store = useClusterStore()
    await store.hydrate()
    store.startPolling()
    store.reset()
    expect(store.mode).toBe("standalone")
    expect(store.sites).toEqual([])
    expect(intervalCtl.stop).toHaveBeenCalled()
  })
})
