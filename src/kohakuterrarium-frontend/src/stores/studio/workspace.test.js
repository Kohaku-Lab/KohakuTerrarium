/**
 * Studio workspace store — pure state management.
 *
 * The API calls are mocked; we only verify the store's state
 * transitions + recent-list persistence behavior.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/studio/api", () => ({
  workspaceAPI: {
    open: vi.fn(),
    get: vi.fn(),
    close: vi.fn(),
  },
}))

import { workspaceAPI } from "@/utils/studio/api"
import { useStudioWorkspaceStore } from "./workspace.js"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear?.()
  vi.clearAllMocks()
})

const summary = {
  root: "/tmp/ws",
  creatures: [{ name: "alpha" }],
  modules: {
    tools: [],
    subagents: [],
    triggers: [],
    plugins: [],
    inputs: [],
    outputs: [],
  },
}

describe("studio workspace store", () => {
  it("starts closed", () => {
    const store = useStudioWorkspaceStore()
    expect(store.isOpen).toBe(false)
    expect(store.creatures).toEqual([])
  })

  it("open() sets root + summary", async () => {
    workspaceAPI.open.mockResolvedValueOnce(summary)
    const store = useStudioWorkspaceStore()
    await store.open("/tmp/ws")
    expect(store.isOpen).toBe(true)
    expect(store.root).toBe("/tmp/ws")
    expect(store.creatures).toHaveLength(1)
  })

  it("open() pushes to recent", async () => {
    workspaceAPI.open.mockResolvedValueOnce(summary)
    const store = useStudioWorkspaceStore()
    await store.open("/tmp/ws")
    expect(store.recent[0]).toBe("/tmp/ws")
  })

  it("open() dedupes recent", async () => {
    workspaceAPI.open.mockResolvedValue(summary)
    const store = useStudioWorkspaceStore()
    await store.open("/tmp/ws")
    await store.open("/tmp/ws")
    const count = store.recent.filter((p) => p === "/tmp/ws").length
    expect(count).toBe(1)
  })

  it("open() moves existing entry to front", async () => {
    workspaceAPI.open.mockImplementation((path) => Promise.resolve({ ...summary, root: path }))
    const store = useStudioWorkspaceStore()
    await store.open("/a")
    await store.open("/b")
    await store.open("/a")
    expect(store.recent[0]).toBe("/a")
    expect(store.recent[1]).toBe("/b")
  })

  it("close() clears state", async () => {
    workspaceAPI.open.mockResolvedValueOnce(summary)
    workspaceAPI.close.mockResolvedValueOnce(undefined)
    const store = useStudioWorkspaceStore()
    await store.open("/tmp/ws")
    await store.close()
    expect(store.isOpen).toBe(false)
    expect(store.summary).toBeNull()
  })

  it("open() captures error when backend rejects", async () => {
    const err = Object.assign(new Error("boom"), { status: 400, code: "not_found" })
    workspaceAPI.open.mockRejectedValueOnce(err)
    const store = useStudioWorkspaceStore()
    await expect(store.open("/bad")).rejects.toThrow("boom")
    expect(store.error).toBe(err)
    expect(store.isOpen).toBe(false)
  })

  it("hydrate() handles 409 no-workspace cleanly", async () => {
    const err = Object.assign(new Error("no workspace"), { status: 409 })
    workspaceAPI.get.mockRejectedValueOnce(err)
    const store = useStudioWorkspaceStore()
    await store.hydrate()
    expect(store.isOpen).toBe(false)
    // 409 doesn't set the error ref — it's a normal "no workspace" state
    expect(store.error).toBeNull()
  })

  it("clearRecent() wipes the list", async () => {
    workspaceAPI.open.mockResolvedValueOnce(summary)
    const store = useStudioWorkspaceStore()
    await store.open("/tmp/ws")
    store.clearRecent()
    expect(store.recent).toEqual([])
  })
})
