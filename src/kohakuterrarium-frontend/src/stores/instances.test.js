import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    sessionAPI: {
      listActive: vi.fn(),
      getActive: vi.fn(),
      stopActive: vi.fn(),
    },
    agentAPI: {
      create: vi.fn(),
    },
    terrariumAPI: {
      create: vi.fn(),
    },
  }
})

import { sessionAPI } from "@/utils/api"
import { useInstancesStore } from "./instances"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("instances store", () => {
  it("clears stale current instance on fetchOne 404", async () => {
    const store = useInstancesStore()
    store.list = [{ id: "graph_dead", type: "creature" }]
    store.current = { id: "graph_dead", type: "creature" }
    sessionAPI.getActive.mockRejectedValue({ response: { status: 404 } })

    const result = await store.fetchOne("graph_dead")

    expect(result).toBeNull()
    expect(store.current).toBeNull()
    expect(store.list).toEqual([])
  })

  it("maps a unified Session payload to a terrarium-shaped instance", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockResolvedValue({
      session_id: "graph_team",
      name: "team",
      pwd: "/repo",
      has_root: true,
      created_at: "2024",
      config_path: "team.yaml",
      creatures: [
        {
          name: "root",
          creature_id: "root_abc",
          model: "model",
          llm_name: "provider/model",
          is_root: true,
          running: true,
          listen_channels: [],
          send_channels: [],
        },
        {
          name: "worker",
          creature_id: "worker_def",
          model: "model2",
          llm_name: "provider/model2",
          running: true,
          listen_channels: [],
          send_channels: [],
        },
      ],
      channels: [],
    })

    const result = await store.fetchOne("graph_team")

    expect(result.id).toBe("graph_team")
    expect(result.graph_id).toBe("graph_team")
    expect(result.type).toBe("terrarium") // 2+ creatures
    expect(result.creatures.length).toBe(2)
    // Primary creature is the root flagged one — drives the model pill.
    expect(result.llm_name).toBe("provider/model")
    expect(store.current.id).toBe("graph_team")
  })

  it("maps a 1-creature Session as a creature-shaped instance", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockResolvedValue({
      session_id: "graph_solo",
      name: "alice",
      pwd: "/repo",
      has_root: false,
      creatures: [
        {
          name: "alice",
          creature_id: "alice_xyz",
          model: "m",
          llm_name: "p/m",
          running: true,
          listen_channels: [],
          send_channels: [],
        },
      ],
      channels: [],
    })

    const result = await store.fetchOne("graph_solo")

    expect(result.type).toBe("creature")
    expect(result.creatures.length).toBe(1)
    expect(result.creatures[0].name).toBe("alice")
  })
})
