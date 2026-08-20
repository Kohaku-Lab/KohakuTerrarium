import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  sessionAPI: {
    getTurns: vi.fn(),
  },
}))

import { useTurnRollupStore } from "./turnRollup"
import { sessionAPI } from "@/utils/api"

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe("turn rollup bounded pagination", () => {
  it("loads the latest window when the session exceeds the page size", async () => {
    sessionAPI.getTurns
      .mockResolvedValueOnce({
        agent: "alice",
        turns: [{ turn_index: 1 }],
        total: 1001,
      })
      .mockResolvedValueOnce({
        agent: "alice",
        turns: [{ turn_index: 2 }, { turn_index: 1001 }],
        total: 1001,
        offset: 1,
      })

    const store = useTurnRollupStore()
    await store.load("session-a", "alice")

    expect(sessionAPI.getTurns).toHaveBeenNthCalledWith(1, "session-a", {
      agent: "alice",
      limit: 1000,
      offset: 0,
      aggregate: false,
    })
    expect(sessionAPI.getTurns).toHaveBeenNthCalledWith(2, "session-a", {
      agent: "alice",
      limit: 1000,
      offset: 1,
      aggregate: false,
    })
    expect(store.turns.map((turn) => turn.turn_index)).toEqual([2, 1001])
    expect(store.windowOffset).toBe(1)
    expect(store.hasOlder).toBe(true)
  })

  it("settles loading after the server resolves the default agent", async () => {
    sessionAPI.getTurns.mockResolvedValue({
      agent: "alice",
      turns: [{ turn_index: 1 }],
      total: 1,
      offset: 0,
    })

    const store = useTurnRollupStore()
    await store.load("session-a")

    expect(store.agent).toBe("alice")
    expect(store.loading).toBe(false)
  })

  it("prepends the previous bounded page", async () => {
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.total = 1500
    store.windowOffset = 500
    store.turns = [{ turn_index: 501 }, { turn_index: 1500 }]
    sessionAPI.getTurns.mockResolvedValue({
      agent: "alice",
      turns: [{ turn_index: 1 }, { turn_index: 500 }],
      total: 1500,
      offset: 0,
    })

    await store.loadOlder()

    expect(sessionAPI.getTurns).toHaveBeenCalledWith("session-a", {
      agent: "alice",
      limit: 500,
      offset: 0,
      aggregate: false,
    })
    expect(store.turns.map((turn) => turn.turn_index)).toEqual([1, 500, 501, 1500])
    expect(store.windowOffset).toBe(0)
    expect(store.hasOlder).toBe(false)
  })

  it("fetches and inserts a missing timeline turn exactly once", async () => {
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.turns = [{ turn_index: 1001 }]
    sessionAPI.getTurns.mockResolvedValue({
      agent: "alice",
      turns: [{ turn_index: 5 }],
      total: 1,
    })

    await expect(store.ensureTurn(5)).resolves.toBe(true)
    await expect(store.ensureTurn(5)).resolves.toBe(true)

    expect(sessionAPI.getTurns).toHaveBeenCalledTimes(1)
    expect(sessionAPI.getTurns).toHaveBeenCalledWith("session-a", {
      agent: "alice",
      fromTurn: 5,
      toTurn: 5,
      limit: 1000,
      offset: 0,
      aggregate: false,
    })
    expect(store.turns.map((turn) => turn.turn_index)).toEqual([5, 1001])
  })

  it("drops an exact-turn response after the active scope changes", async () => {
    const pending = deferred()
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.turns = [{ turn_index: 1001 }]
    sessionAPI.getTurns.mockReturnValue(pending.promise)

    const request = store.ensureTurn(5)
    store.sessionName = "session-b"
    store.agent = "bob"
    store.turns = [{ turn_index: 8 }]
    pending.resolve({ agent: "alice", turns: [{ turn_index: 5 }], total: 1 })

    await expect(request).resolves.toBe(false)
    expect(store.turns).toEqual([{ turn_index: 8 }])
  })

  it("drops an older-page response after the active scope changes", async () => {
    const pending = deferred()
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.total = 1500
    store.windowOffset = 500
    store.turns = [{ turn_index: 501 }]
    sessionAPI.getTurns.mockReturnValue(pending.promise)

    const request = store.loadOlder()
    store.sessionName = "session-b"
    store.agent = "bob"
    store.turns = [{ turn_index: 8 }]
    pending.resolve({ agent: "alice", turns: [{ turn_index: 1 }], total: 1500 })

    await expect(request).resolves.toBe(false)
    expect(store.turns).toEqual([{ turn_index: 8 }])
  })

  it("drops an older-page response after the same scope reloads", async () => {
    const pending = deferred()
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.total = 1500
    store.windowOffset = 500
    store.turns = [{ turn_index: 501 }]
    sessionAPI.getTurns.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({
      agent: "alice",
      turns: [{ turn_index: 700 }],
      total: 1,
      offset: 0,
    })

    const olderRequest = store.loadOlder()
    await store.load("session-a", "alice")
    pending.resolve({ agent: "alice", turns: [{ turn_index: 1 }], total: 1500 })

    await expect(olderRequest).resolves.toBe(false)
    expect(store.turns).toEqual([{ turn_index: 700 }])
    expect(store.loadingOlder).toBe(false)
  })

  it("drops an exact-turn response after the same scope reloads", async () => {
    const pending = deferred()
    const store = useTurnRollupStore()
    store.sessionName = "session-a"
    store.agent = "alice"
    store.turns = [{ turn_index: 1001 }]
    sessionAPI.getTurns.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({
      agent: "alice",
      turns: [{ turn_index: 700 }],
      total: 1,
      offset: 0,
    })

    const exactRequest = store.ensureTurn(5)
    await store.load("session-a", "alice")
    pending.resolve({ agent: "alice", turns: [{ turn_index: 5 }], total: 1 })

    await expect(exactRequest).resolves.toBe(false)
    expect(store.turns).toEqual([{ turn_index: 700 }])
  })
})
