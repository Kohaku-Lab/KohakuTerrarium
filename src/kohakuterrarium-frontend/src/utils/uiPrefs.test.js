import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getUIPrefs: vi.fn(async () => ({ values: {} })),
    updateUIPrefs: vi.fn(async (values) => ({ values })),
  },
}))

import { settingsAPI } from "@/utils/api"
import {
  _resetUIPrefsForTests,
  ensureUIPrefsLoaded,
  getHybridPrefSync,
  readLocalPref,
  removeHybridPref,
  setHybridPref,
  writeLocalPref,
} from "./uiPrefs.js"

let storage

beforeEach(() => {
  _resetUIPrefsForTests()
  vi.clearAllMocks()
  settingsAPI.getUIPrefs.mockImplementation(async () => ({ values: {} }))
  settingsAPI.updateUIPrefs.mockImplementation(async (values) => ({ values }))
  vi.useFakeTimers()
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe("uiPrefs backend flush — coalescing", () => {
  it("coalesces a burst of writes into a single request after idle", async () => {
    for (let i = 0; i < 50; i++) setHybridPref("draftish", `value-${i}`)
    setHybridPref("theme", "dark")

    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1499)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2)

    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith({
      draftish: "value-49",
      theme: "dark",
    })
  })

  it("resets the idle timer on each write but honours the max-wait deadline", async () => {
    // A write every second keeps the 1.5s idle debounce from ever
    // firing — the 10s max-wait must checkpoint anyway.
    setHybridPref("k", "v0")
    for (let i = 1; i <= 9; i++) {
      await vi.advanceTimersByTimeAsync(1000)
      setHybridPref("k", `v${i}`)
    }
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith({ k: "v9" })
  })

  it("skips no-op writes that match the backend value", async () => {
    settingsAPI.getUIPrefs.mockResolvedValueOnce({ values: { theme: "dark" } })
    await ensureUIPrefsLoaded()

    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(5000)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()

    setHybridPref("theme", "light")
    await vi.advanceTimersByTimeAsync(5000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith({ theme: "light" })

    // The optimistic cache now holds "light" — repeating it is a no-op.
    setHybridPref("theme", "light")
    await vi.advanceTimersByTimeAsync(5000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
  })

  it("compares json values structurally for no-op detection", async () => {
    settingsAPI.getUIPrefs.mockResolvedValueOnce({ values: { "kt.splitPane": { a: 30 } } })
    await ensureUIPrefsLoaded()

    setHybridPref("kt.splitPane", { a: 30 }, { json: true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()

    setHybridPref("kt.splitPane", { a: 55 }, { json: true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
  })
})

describe("uiPrefs backend flush — failure handling", () => {
  it("re-queues and retries after a transient failure", async () => {
    settingsAPI.updateUIPrefs.mockRejectedValueOnce({ response: { status: 500 } })

    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)

    // Retry lands FLUSH_RETRY_MS later with the same payload.
    await vi.advanceTimersByTimeAsync(10000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(2)
    expect(settingsAPI.updateUIPrefs).toHaveBeenLastCalledWith({ theme: "dark" })
  })

  it("a newer write during a failed flush wins over the re-queued value", async () => {
    let rejectFirst
    settingsAPI.updateUIPrefs.mockImplementationOnce(
      () => new Promise((_, reject) => (rejectFirst = reject)),
    )

    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)

    setHybridPref("theme", "light")
    rejectFirst({ response: { status: 500 } })
    await vi.advanceTimersByTimeAsync(10000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(2)
    expect(settingsAPI.updateUIPrefs).toHaveBeenLastCalledWith({ theme: "light" })
  })

  it("stops auto-retrying after repeated failures until a new write arrives", async () => {
    settingsAPI.updateUIPrefs.mockRejectedValue({ response: { status: 500 } })

    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(1500) // first attempt
    for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(10000)
    // 1 initial + 4 retries = FLUSH_MAX_RETRIES attempts, then quiet —
    // a dead backend must not be polled forever from an idle tab.
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(5)

    // A fresh user write re-arms the flush loop.
    settingsAPI.updateUIPrefs.mockImplementation(async (values) => ({ values }))
    setHybridPref("theme", "light")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(6)
    expect(settingsAPI.updateUIPrefs).toHaveBeenLastCalledWith({ theme: "light" })
  })

  it("keeps the in-memory cache coherent after writes are disabled", async () => {
    settingsAPI.updateUIPrefs.mockRejectedValueOnce({ response: { status: 404 } })
    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(1500)

    setHybridPref("theme", "light")
    // Simulate reads without localStorage backing — the in-memory
    // cache must still serve the latest value.
    storage.clear()
    expect(getHybridPrefSync("theme", "system")).toBe("light")
  })

  it("disables backend writes permanently on 404", async () => {
    settingsAPI.updateUIPrefs.mockRejectedValueOnce({ response: { status: 404 } })

    setHybridPref("theme", "dark")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)

    setHybridPref("theme", "light")
    await vi.advanceTimersByTimeAsync(60000)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
    // Local storage still works.
    expect(readLocalPref("theme")).toBe("light")
  })

  it("flushes writes that arrived while a request was in flight", async () => {
    let resolveFirst
    settingsAPI.updateUIPrefs.mockImplementationOnce(
      () => new Promise((resolve) => (resolveFirst = resolve)),
    )

    setHybridPref("a", "1")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)

    setHybridPref("b", "2")
    await vi.advanceTimersByTimeAsync(1500)
    // Still one call — the second flush must wait for the first.
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)

    resolveFirst({ values: { a: "1" } })
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(2)
    expect(settingsAPI.updateUIPrefs).toHaveBeenLastCalledWith({ b: "2" })
  })
})

describe("uiPrefs removes", () => {
  it("skips the request when removing a key the backend never had", async () => {
    await ensureUIPrefsLoaded()
    removeHybridPref("kt.chat.draft.some-instance.main")
    await vi.advanceTimersByTimeAsync(60000)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
  })

  it("sends null for keys the backend does hold", async () => {
    settingsAPI.getUIPrefs.mockResolvedValueOnce({ values: { stale: "x" } })
    await ensureUIPrefsLoaded()
    removeHybridPref("stale")
    await vi.advanceTimersByTimeAsync(1500)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledTimes(1)
    expect(settingsAPI.updateUIPrefs).toHaveBeenCalledWith({ stale: null })
  })
})

describe("uiPrefs reads", () => {
  it("getHybridPrefSync treats a backend null as absent", async () => {
    settingsAPI.getUIPrefs.mockResolvedValueOnce({ values: { gone: null } })
    await ensureUIPrefsLoaded()
    expect(getHybridPrefSync("gone", "fallback")).toBe("fallback")
  })

  it("local-only helpers never touch the network", async () => {
    writeLocalPref("kt.chat.draft.inst.main", "half-typed message")
    expect(readLocalPref("kt.chat.draft.inst.main")).toBe("half-typed message")
    writeLocalPref("kt.chat.draft.inst.main", null)
    expect(readLocalPref("kt.chat.draft.inst.main")).toBeNull()
    await vi.advanceTimersByTimeAsync(60000)
    expect(settingsAPI.updateUIPrefs).not.toHaveBeenCalled()
    expect(settingsAPI.getUIPrefs).not.toHaveBeenCalled()
  })

  it("ensureUIPrefsLoaded with timeoutMs resolves even when the backend hangs", async () => {
    settingsAPI.getUIPrefs.mockImplementationOnce(() => new Promise(() => {}))
    const race = ensureUIPrefsLoaded({ timeoutMs: 2500 })
    await vi.advanceTimersByTimeAsync(2500)
    await expect(race).resolves.toEqual({})
  })
})
