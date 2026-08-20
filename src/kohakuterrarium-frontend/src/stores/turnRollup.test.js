import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useTurnRollupStore } from "@/stores/turnRollup"
import { sessionAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  sessionAPI: { getTurns: vi.fn() },
}))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}

describe("turn rollup store", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionAPI.getTurns.mockReset()
  })

  it("ignores a stale response after switching agents", async () => {
    const oldRequest = deferred()
    sessionAPI.getTurns
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValueOnce({ agent: "bob", turns: [{ turn_index: 2 }], total: 1 })
    const store = useTurnRollupStore("turn-rollup-race")

    const firstLoad = store.load("session", "alice")
    await Promise.resolve()
    await store.load("session", "bob")
    oldRequest.resolve({ agent: "alice", turns: [{ turn_index: 1 }], total: 1 })
    await firstLoad

    expect(store.agent).toBe("bob")
    expect(store.turns).toEqual([{ turn_index: 2 }])
    expect(store.total).toBe(1)
    expect(store.error).toBe("")
    expect(store.loading).toBe(false)
  })

  it("invalidates an in-flight response when cleared", async () => {
    const request = deferred()
    sessionAPI.getTurns.mockReturnValueOnce(request.promise)
    const store = useTurnRollupStore("turn-rollup-clear")

    const load = store.load("session", "alice")
    await Promise.resolve()
    store.clear()
    request.resolve({ agent: "alice", turns: [{ turn_index: 1 }], total: 1 })
    await load

    expect(store.sessionName).toBe("")
    expect(store.agent).toBe("")
    expect(store.turns).toEqual([])
    expect(store.loading).toBe(false)
  })
})
