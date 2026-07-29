import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    sessionAPI: {
      listActive: vi.fn(),
      listOpen: vi.fn(),
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
import { useConversationsStore } from "./conversations"
import { useInstancesStore } from "./instances"

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  sessionAPI.listOpen.mockResolvedValue([])
  sessionAPI.stopActive.mockResolvedValue({ status: "stopped" })
})

describe("instances.fetchAll — SessionListing payload shape", () => {
  it("handles the listing payload where `creatures` is an int (not an array)", async () => {
    // Audit-loop regression: ``GET /api/sessions/active`` returns
    // ``SessionListing.to_dict()`` whose ``creatures`` field is an
    // INT count, not a creature array.  The pre-fix mapper did
    // ``(data.creatures || []).map(...)`` which crashed on ``int.map``
    // → silent fetchAll failure → empty dashboard for any session
    // with ≥1 creature.  This test pins the recovery.
    const store = useInstancesStore()
    sessionAPI.listActive.mockResolvedValue([
      {
        session_id: "graph_alice",
        name: "alice",
        running: true,
        creatures: 1,
        node_id: "_host",
      },
    ])
    await store.fetchAll()
    expect(store.list).toHaveLength(1)
    expect(store.list[0].id).toBe("graph_alice")
    expect(store.list[0].home_node).toBe("_host")
  })

  it("preserves worker home_node from listing's node_id", async () => {
    const store = useInstancesStore()
    sessionAPI.listActive.mockResolvedValue([
      {
        session_id: "graph_remote",
        name: "alice",
        running: true,
        creatures: 1,
        node_id: "worker-1",
      },
    ])
    await store.fetchAll()
    expect(store.list[0].home_node).toBe("worker-1")
  })
})

describe("instances store", () => {
  it("invalidates a pre-stop list request and hides the stopped conversation immediately", async () => {
    const store = useInstancesStore()
    const conversations = useConversationsStore()
    const deferred = promiseWithResolvers()
    sessionAPI.listActive.mockReturnValue(deferred.promise)
    sessionAPI.listOpen.mockReturnValue(new Promise(() => {}))
    store.list = [{ id: "graph_dead", status: "running" }]
    store.current = { id: "graph_dead", status: "running" }
    conversations.rows = [
      {
        id: "conversation-one",
        runtime_id: "graph_dead",
        is_live: true,
        status: "running",
      },
    ]

    const staleFetch = store.fetchAll()
    await store.stop("graph_dead")

    expect(store.list).toEqual([])
    expect(store.current).toBeNull()
    expect(conversations.liveRows).toEqual([])

    deferred.resolve([
      {
        session_id: "graph_dead",
        name: "stale",
        creatures: 1,
      },
    ])
    await staleFetch

    expect(store.list).toEqual([])
    expect(store.current).toBeNull()
  })

  it("ignores a pre-stop detail response that resolves after stop", async () => {
    const store = useInstancesStore()
    const deferred = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValue(deferred.promise)
    store.list = [{ id: "graph_dead", status: "running" }]
    store.current = { id: "graph_dead", status: "running" }

    const staleFetch = store.fetchOne("graph_dead")
    await store.stop("graph_dead")
    deferred.resolve({
      session_id: "graph_dead",
      name: "stale",
      creatures: [],
      channels: [],
    })
    await staleFetch

    expect(store.list).toEqual([])
    expect(store.current).toBeNull()
  })

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

function promiseWithResolvers() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
