import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/api", () => {
  return {
    sessionAPI: {
      listActive: vi.fn(),
      getActive: vi.fn(),
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
  it("invalidates a pre-stop list request and removes the stopped runtime immediately", async () => {
    const store = useInstancesStore()
    const deferred = promiseWithResolvers()
    sessionAPI.listActive.mockReturnValue(deferred.promise)
    store.list = [{ id: "graph_dead", status: "running" }]
    store.current = { id: "graph_dead", status: "running" }

    const staleFetch = store.fetchAll()
    store.markRuntimeStopped("graph_dead")

    expect(store.list).toEqual([])
    expect(store.current).toBeNull()

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

  it("does not return a pre-stop detail response after the runtime is removed", async () => {
    const store = useInstancesStore()
    const deferred = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValue(deferred.promise)
    store.list = [{ id: "graph_dead", status: "running" }]
    store.current = { id: "graph_dead", status: "running" }

    const staleFetch = store.fetchOne("graph_dead")
    store.markRuntimeStopped("graph_dead")
    deferred.resolve({
      session_id: "graph_dead",
      name: "stale",
      creatures: [],
      channels: [],
    })
    const result = await staleFetch

    expect(result).toBeNull()
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

  it("shares one in-flight request between concurrent fetchOne callers for the same id", async () => {
    const store = useInstancesStore()
    const deferred = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValue(deferred.promise)

    // Route navigation and the 5 s attach-tab poll racing on the same
    // instance must not stack duplicate lookups.
    const first = store.fetchOne("graph_slow")
    const second = store.fetchOne("graph_slow")
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(1)

    deferred.resolve({ session_id: "graph_slow", name: "slow", creatures: 0, channels: [] })
    const [a, b] = await Promise.all([first, second])
    expect(a).toBe(b)
    expect(store.current.id).toBe("graph_slow")

    // A different id still gets its own request.
    sessionAPI.getActive.mockResolvedValue({
      session_id: "graph_other",
      name: "other",
      creatures: 0,
      channels: [],
    })
    await store.fetchOne("graph_other")
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(2)
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
          config_name: "root-config",
          config_ref: "creatures/root.yaml",
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
    expect(result.session_name).toBe("team")
    expect(result.config_name).toBe("team")
    expect(result.creature_config_name).toBe("root-config")
    expect(result.config_ref).toBe("creatures/root.yaml")
    expect(result.creatures[0]).toMatchObject({
      config_name: "root-config",
      config_ref: "creatures/root.yaml",
    })
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
