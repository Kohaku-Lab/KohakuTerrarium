import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    agentAPI: {
      get: vi.fn(),
      list: vi.fn(),
      stop: vi.fn(),
      create: vi.fn(),
    },
    terrariumAPI: {
      get: vi.fn(),
      list: vi.fn(),
      stop: vi.fn(),
      create: vi.fn(),
    },
  }
})

import { agentAPI, terrariumAPI } from "@/utils/api"
import { useInstancesStore } from "./instances"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("instances store", () => {
  it("clears stale current instance on fetchOne 404", async () => {
    const store = useInstancesStore()
    store.list = [{ id: "agent_dead", type: "creature" }]
    store.current = { id: "agent_dead", type: "creature" }
    agentAPI.get.mockRejectedValue({ response: { status: 404 } })

    const result = await store.fetchOne("agent_dead")

    expect(result).toBeNull()
    expect(store.current).toBeNull()
    expect(store.list).toEqual([])
  })

  it("returns mapped terrarium when fetchOne succeeds", async () => {
    const store = useInstancesStore()
    terrariumAPI.get.mockResolvedValue({
      terrarium_id: "terrarium_1",
      name: "team",
      pwd: "/repo",
      running: true,
      has_root: true,
      root_model: "model",
      root_session_id: "sess",
      root_max_context: 10,
      root_compact_threshold: 5,
      creatures: { worker: { running: true, listen_channels: [], send_channels: [] } },
      channels: [],
    })

    const result = await store.fetchOne("terrarium_1")

    expect(result.id).toBe("terrarium_1")
    expect(store.current.id).toBe("terrarium_1")
  })
})
