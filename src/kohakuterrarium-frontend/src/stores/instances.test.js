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

import { useHostsStore } from "./hosts"

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

describe("session summary/detail ownership", () => {
  function detail(id = "a", names = ["alice"]) {
    return {
      session_id: id,
      name: id,
      pwd: "/workspace",
      creatures: names.map((name) => ({ name, llm_name: "codex/current", model: "current" })),
      channels: [],
    }
  }
  function summary(id = "a", count = 1) {
    return { session_id: id, name: `${id}-renamed`, creatures: count, node_id: "_host" }
  }
  it("preserves loaded details for every session through list polling", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockImplementation(async (id) => detail(id))
    await store.fetchOne("a")
    await store.fetchOne("b")
    sessionAPI.listActive.mockResolvedValue([summary("a"), summary("b")])
    await store.fetchAll()
    expect(store.list.map((item) => item.creatures[0]?.name)).toEqual(["alice", "alice"])
    expect(store.list[0].pwd).toBe("/workspace")
    expect(store.list[0].session_name).toBe("a-renamed")
    expect(store.current).toEqual(store.list[1])
  })
  it.each(["list-first", "detail-first"])(
    "accepts valid detail across ordinary polling: %s",
    async (order) => {
      const store = useInstancesStore()
      const list = promiseWithResolvers(),
        full = promiseWithResolvers()
      sessionAPI.getActive.mockReturnValue(full.promise)
      sessionAPI.listActive.mockReturnValue(list.promise)
      const pendingDetail = store.fetchOne("a")
      const pendingList = store.fetchAll()
      if (order === "list-first") {
        list.resolve([summary()])
        await pendingList
        full.resolve(detail())
      } else {
        full.resolve(detail())
        await pendingDetail
        list.resolve([summary()])
      }
      await Promise.all([pendingDetail, pendingList])
      expect((await pendingDetail).creatures[0]?.name).toBe("alice")
      expect(store.current.creatures[0]?.name).toBe("alice")
      expect(store.list[0].creatures[0]?.name).toBe("alice")
    },
  )
  it("records a changed summary count without inventing a new roster; explicit empty detail clears it", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockResolvedValue(detail())
    await store.fetchOne("a")
    sessionAPI.listActive.mockResolvedValue([summary("a", 2)])
    await store.fetchAll()
    expect(store.current.creatures.map((c) => c.name)).toEqual(["alice"])
    expect(store.current.creature_count).toBe(2)
    sessionAPI.getActive.mockResolvedValue(detail("a", []))
    await store.fetchOne("a")
    sessionAPI.listActive.mockResolvedValue([summary("a", 0)])
    await store.fetchAll()
    expect(store.current.creatures).toEqual([])
    expect(store.current.creature_count).toBe(0)
  })
  it("clears current when the session disappears from the listing", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockResolvedValue(detail())
    await store.fetchOne("a")
    sessionAPI.listActive.mockResolvedValue([])
    await store.fetchAll()
    expect(store.current).toBeNull()
  })
  it("does not rejoin an invalidated detail or let its cleanup remove the replacement", async () => {
    const store = useInstancesStore()
    const old = promiseWithResolvers(),
      fresh = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const oldRequest = store.fetchOne("a")
    store.markRuntimeStopped("a")
    const newRequest = store.fetchOne("a")
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(2)
    old.resolve(detail())
    expect(await oldRequest).toBeNull()
    const joined = store.fetchOne("a")
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(2)
    fresh.resolve(detail("a", ["new-alice"]))
    expect((await newRequest).creatures[0].name).toBe("new-alice")
    expect(await joined).toBe(await newRequest)
  })
  it.each(["stop", "404"])(
    "keeps unrelated detail requests shared and valid after %s",
    async (reason) => {
      const store = useInstancesStore()
      const alive = promiseWithResolvers()
      sessionAPI.getActive.mockImplementation((id) =>
        id === "a" ? alive.promise : Promise.reject({ response: { status: 404 } }),
      )
      const first = store.fetchOne("a")
      if (reason === "stop") store.markRuntimeStopped("b")
      else expect(await store.fetchOne("b")).toBeNull()
      const joined = store.fetchOne("a")
      expect(sessionAPI.getActive.mock.calls.filter(([id]) => id === "a")).toHaveLength(1)
      alive.resolve(detail())
      expect((await first).creatures[0].name).toBe("alice")
      expect(await joined).toBe(await first)
      expect(store.current.id).toBe("a")
    },
  )
  it("rejects a late detail looked up by creature id after its canonical session stops", async () => {
    const store = useInstancesStore()
    const pending = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValue(pending.promise)
    const request = store.fetchOne("alice-id")
    store.markRuntimeStopped("a")
    store.markRuntimeStopped("b")
    pending.resolve(detail())
    expect(await request).toBeNull()
    expect(store.list).toEqual([])
  })
  it.each(["missing", "outdated"])(
    "keeps newer detail when an older listing is %s",
    async (state) => {
      const store = useInstancesStore()
      const list = promiseWithResolvers()
      sessionAPI.listActive.mockReturnValueOnce(list.promise)
      const pending = store.fetchAll()
      sessionAPI.getActive.mockResolvedValue(detail("a", ["alice", "bob"]))
      const loaded = await store.fetchOne("a")
      list.resolve(state === "missing" ? [] : [summary("a", 1)])
      await pending
      expect(store.current).toEqual(loaded)
      expect(store.list[0].creature_count).toBe(2)
      expect(store.list[0].creatures.map((c) => c.name)).toEqual(["alice", "bob"])
      expect(store.list).toHaveLength(1)
      sessionAPI.listActive.mockResolvedValue([])
      await store.fetchAll()
      expect(store.list).toEqual([])
      expect(store.current).toBeNull()
    },
  )
  it("retains every refreshed session but removes unrefreshed sessions absent from an old list", async () => {
    const store = useInstancesStore()
    sessionAPI.getActive.mockImplementation(async (id) => detail(id))
    await store.fetchOne("a")
    await store.fetchOne("b")
    await store.fetchOne("gone")
    const list = promiseWithResolvers()
    sessionAPI.listActive.mockReturnValue(list.promise)
    const pending = store.fetchAll()
    await store.fetchOne("a")
    await store.fetchOne("b")
    list.resolve([])
    await pending
    expect(store.list.map((item) => item.id).sort()).toEqual(["a", "b"])
    expect(store.current.id).toBe("b")
  })
  it("updates summary-only classification as the graph grows and shrinks", async () => {
    const store = useInstancesStore()
    for (const count of [1, 2, 1]) {
      sessionAPI.listActive.mockResolvedValue([summary("a", count)])
      await store.fetchAll()
      expect(store.list[0].type).toBe(count > 1 ? "terrarium" : "creature")
      expect(store.list[0].creatures).toEqual([])
    }
  })
  it("isolates pending detail and cached metadata across hosts with the same session id", async () => {
    const store = useInstancesStore(),
      hosts = useHostsStore()
    const old = promiseWithResolvers()
    sessionAPI.getActive.mockReturnValueOnce(old.promise)
    const oldRequest = store.fetchOne("a")
    hosts.activeHostId = "other-host"
    sessionAPI.getActive.mockResolvedValue(detail("a", ["other-alice"]))
    const otherRequest = store.fetchOne("a")
    expect(sessionAPI.getActive).toHaveBeenCalledTimes(2)
    expect((await otherRequest).creatures[0].name).toBe("other-alice")
    old.resolve(detail())
    expect(await oldRequest).toBeNull()
    expect(store.current.creatures[0].name).toBe("other-alice")
    hosts.activeHostId = null
    sessionAPI.listActive.mockResolvedValue([summary()])
    await store.fetchAll()
    expect(store.current).toBeNull()
    expect(store.list[0].creatures).toEqual([])
  })
})

it("does not resurrect a missing session from a listing requested before its detail 404", async () => {
  const store = useInstancesStore()
  const list = promiseWithResolvers()
  sessionAPI.listActive.mockReturnValue(list.promise)
  const pending = store.fetchAll()
  sessionAPI.getActive.mockRejectedValue({ response: { status: 404 } })
  expect(await store.fetchOne("gone")).toBeNull()
  list.resolve([{ session_id: "gone", name: "gone", creatures: 1 }])
  await pending
  expect(store.list).toEqual([])
  expect(store.current).toBeNull()
})
