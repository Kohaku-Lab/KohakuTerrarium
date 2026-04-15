import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    agentAPI: {
      getScratchpad: vi.fn(),
      patchScratchpad: vi.fn(),
    },
    terrariumAPI: {
      getScratchpad: vi.fn(),
      patchScratchpad: vi.fn(),
    },
  }
})

import { agentAPI, terrariumAPI } from "@/utils/api"
import { useScratchpadStore } from "./scratchpad"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("scratchpad store", () => {
  it("uses terrarium scratchpad endpoint when target is provided", async () => {
    const store = useScratchpadStore()
    terrariumAPI.getScratchpad.mockResolvedValue({ answer: "42" })

    await store.fetch("terrarium_1", "worker")

    expect(terrariumAPI.getScratchpad).toHaveBeenCalledWith("terrarium_1", "worker")
    expect(store.getFor("terrarium_1", "worker")).toEqual({ answer: "42" })
  })

  it("uses agent scratchpad endpoint without terrarium target", async () => {
    const store = useScratchpadStore()
    agentAPI.getScratchpad.mockResolvedValue({ note: "ok" })

    await store.fetch("agent_1")

    expect(agentAPI.getScratchpad).toHaveBeenCalledWith("agent_1")
    expect(store.getFor("agent_1")).toEqual({ note: "ok" })
  })
})
