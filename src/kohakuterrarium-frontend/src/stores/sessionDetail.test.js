import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useSessionDetailStore } from "./sessionDetail.js"

vi.mock("@/utils/api", () => ({
  sessionAPI: {
    getHistoryIndex: vi.fn(),
    getTree: vi.fn().mockResolvedValue(null),
    getSummary: vi.fn().mockResolvedValue(null),
  },
}))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("sessionDetail store — agents getter (UXI-03)", () => {
  it("enumerates every creature, not just root, unioning summary + targets", () => {
    const detail = useSessionDetailStore("s1")
    detail.meta = { agents: ["root"] }
    detail.targets = ["root", "worker_a", "ch:team"]
    detail.summary = { agents: ["root", "worker_b"] }
    // Summary first (viewer-default ordering), then targets, deduped;
    // the ``ch:`` channel target is excluded.
    expect(detail.agents).toEqual(["root", "worker_b", "worker_a"])
    expect(detail.primaryAgent).toBe("root")
  })

  it("falls back to meta.agents before summary/targets load", () => {
    const detail = useSessionDetailStore("s2")
    detail.meta = { agents: ["alice", "bob"] }
    detail.targets = []
    detail.summary = null
    expect(detail.agents).toEqual(["alice", "bob"])
  })

  it("returns an empty list when nothing is loaded", () => {
    const detail = useSessionDetailStore("s3")
    expect(detail.agents).toEqual([])
    expect(detail.primaryAgent).toBeNull()
  })
})

describe("sessionDetail store — loadMeta (UXI-01)", () => {
  it("treats a 404 from the live-id index as a benign empty state", async () => {
    const { sessionAPI } = await import("@/utils/api")
    sessionAPI.getHistoryIndex.mockRejectedValueOnce({ response: { status: 404 } })
    const detail = useSessionDetailStore("live1")
    detail.name = "live_graph_new"
    await detail.loadMeta()
    expect(detail.error).toBe("")
    expect(detail.meta).toBeNull()
    expect(detail.targets).toEqual([])
  })

  it("still surfaces a non-404 failure as an error", async () => {
    const { sessionAPI } = await import("@/utils/api")
    sessionAPI.getHistoryIndex.mockRejectedValueOnce({ response: { status: 500 }, message: "boom" })
    const detail = useSessionDetailStore("live2")
    detail.name = "broken_session"
    await detail.loadMeta()
    expect(detail.error).toContain("Failed to load session metadata")
  })
})
