import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    terrariumAPI: {
      getScratchpad: vi.fn(),
      patchScratchpad: vi.fn(),
    },
  }
})

import { terrariumAPI } from "@/utils/api"
import { useScratchpadStore } from "./scratchpad"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("scratchpad store", () => {
  it("routes to /sessions/{sid}/creatures/{target}/scratchpad", async () => {
    const store = useScratchpadStore()
    terrariumAPI.getScratchpad.mockResolvedValue({ answer: "42" })

    await store.fetch("graph_1", "worker")

    expect(terrariumAPI.getScratchpad).toHaveBeenCalledWith("graph_1", "worker")
    expect(store.getFor("graph_1", "worker")).toEqual({ answer: "42" })
  })

  it("requires a target — solo sessions pass their creature's name", async () => {
    const store = useScratchpadStore()
    // No target → no fetch (caller must resolve a creature first).
    await store.fetch("graph_1")
    expect(terrariumAPI.getScratchpad).not.toHaveBeenCalled()
    expect(store.getFor("graph_1")).toEqual({})
  })
})
