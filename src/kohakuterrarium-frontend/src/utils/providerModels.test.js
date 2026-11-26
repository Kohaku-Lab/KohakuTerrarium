import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  PROVIDER_MODELS_TTL_MS,
  _resetProviderModelsForTests,
  invalidateProviderModels,
  isProviderModelsStale,
  loadProviderModels,
  providerModelsEntry,
} from "./providerModels"

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function catalogApi(scope = "") {
  const pending = []
  return {
    pending,
    discoveryScope: () => scope,
    discoverModels: vi.fn(() => {
      const next = deferred()
      pending.push(next)
      return next.promise
    }),
  }
}

const rows = (...ids) => ids.map((id) => ({ id, name: id }))

beforeEach(() => {
  _resetProviderModelsForTests()
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-11-26T10:00:00Z"))
})

afterEach(() => {
  vi.useRealTimers()
})

describe("provider models cache", () => {
  it("serves cached rows instantly and refetches in the background after the TTL", async () => {
    const api = catalogApi()
    const first = loadProviderModels(api, "qwen")
    const entry = providerModelsEntry(api, "qwen")
    expect(entry.loading.value).toBe(true)
    expect(entry.hasLoaded.value).toBe(false)
    api.pending[0].resolve({ models: rows("a"), fetched_at: "2026-11-26T09:59:00+00:00" })
    expect(await first).toEqual(rows("a"))
    expect(entry.fetchedAt.value).toBe("2026-11-26T09:59:00+00:00")
    expect(entry.loading.value).toBe(false)

    vi.advanceTimersByTime(PROVIDER_MODELS_TTL_MS - 1)
    expect(await loadProviderModels(api, "qwen")).toEqual(rows("a"))
    expect(api.discoverModels).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(1)
    expect(isProviderModelsStale(entry)).toBe(true)
    const background = loadProviderModels(api, "qwen")
    expect(api.discoverModels.mock.calls).toEqual([["qwen"], ["qwen"]])
    expect(entry.rows.value).toEqual(rows("a"))
    expect(entry.loading.value).toBe(true)
    api.pending[1].resolve({ models: rows("b") })
    expect(await background).toEqual(rows("b"))
    expect(entry.rows.value).toEqual(rows("b"))
  })

  it("forces a refetch with refresh=true even when fresh", async () => {
    const api = catalogApi()
    const first = loadProviderModels(api, "qwen")
    api.pending[0].resolve({ models: rows("a") })
    await first
    const forced = loadProviderModels(api, "qwen", { refresh: true })
    expect(api.discoverModels.mock.calls).toEqual([["qwen"], ["qwen", { refresh: true }]])
    expect(providerModelsEntry(api, "qwen").rows.value).toEqual(rows("a"))
    api.pending[1].resolve({ models: rows("c") })
    expect(await forced).toEqual(rows("c"))
  })

  it("de-duplicates in-flight requests per provider and lets refresh supersede a plain load", async () => {
    const api = catalogApi()
    const one = loadProviderModels(api, "qwen")
    const two = loadProviderModels(api, "qwen")
    const other = loadProviderModels(api, "mimo")
    expect(one).toBe(two)
    expect(api.discoverModels).toHaveBeenCalledTimes(2)

    const forced = loadProviderModels(api, "qwen", { refresh: true })
    expect(loadProviderModels(api, "qwen", { refresh: true })).toBe(forced)
    expect(loadProviderModels(api, "qwen")).toBe(forced)
    expect(api.discoverModels).toHaveBeenCalledTimes(3)
    api.pending[2].resolve({ models: rows("forced") })
    api.pending[0].resolve({ models: rows("superseded") })
    api.pending[1].resolve({ models: rows("m") })
    await Promise.all([one, forced, other])
    expect(providerModelsEntry(api, "qwen").rows.value).toEqual(rows("forced"))
    expect(providerModelsEntry(api, "mimo").rows.value).toEqual(rows("m"))
  })

  it("keeps the previous rows when a refresh fails and clears the error on success", async () => {
    const api = catalogApi()
    const first = loadProviderModels(api, "qwen")
    api.pending[0].resolve({ models: rows("a") })
    await first
    const failing = loadProviderModels(api, "qwen", { refresh: true })
    api.pending[1].reject({ response: { data: { detail: "Provider is offline" } } })
    expect(await failing).toEqual(rows("a"))
    const entry = providerModelsEntry(api, "qwen")
    expect(entry.rows.value).toEqual(rows("a"))
    expect(entry.failed.value).toBe(true)
    expect(entry.errorDetail.value).toBe("Provider is offline")
    expect(entry.loading.value).toBe(false)

    const retry = loadProviderModels(api, "qwen", { refresh: true })
    api.pending[2].resolve({ models: rows("b") })
    await retry
    expect(entry.failed.value).toBe(false)
    expect(entry.errorDetail.value).toBe("")
  })

  it("retries a first load that failed and reports a synchronous throw", async () => {
    const api = catalogApi()
    const first = loadProviderModels(api, "qwen")
    api.pending[0].reject(new Error("network"))
    expect(await first).toEqual([])
    const entry = providerModelsEntry(api, "qwen")
    expect(entry.failed.value).toBe(true)
    expect(entry.errorDetail.value).toBe("")
    const second = loadProviderModels(api, "qwen")
    expect(api.discoverModels).toHaveBeenCalledTimes(2)
    api.pending[1].resolve({ models: rows("ok") })
    expect(await second).toEqual(rows("ok"))

    const broken = {
      discoverModels: () => {
        throw new Error("boom")
      },
    }
    expect(await loadProviderModels(broken, "qwen")).toEqual([])
    expect(providerModelsEntry(broken, "qwen").failed.value).toBe(true)
  })

  it("invalidates one provider or all, and keeps hosts and API objects apart", async () => {
    const api = catalogApi()
    const loaded = [loadProviderModels(api, "qwen"), loadProviderModels(api, "mimo")]
    api.pending[0].resolve({ models: rows("q") })
    api.pending[1].resolve({ models: rows("m") })
    await Promise.all(loaded)
    const qwen = providerModelsEntry(api, "qwen")
    const mimo = providerModelsEntry(api, "mimo")

    invalidateProviderModels("qwen")
    const fresh = providerModelsEntry(api, "qwen")
    expect(fresh).not.toBe(qwen)
    expect(fresh.rows.value).toEqual([])
    expect(providerModelsEntry(api, "mimo")).toBe(mimo)
    invalidateProviderModels()
    expect(providerModelsEntry(api, "mimo")).not.toBe(mimo)

    expect(providerModelsEntry(catalogApi(), "mimo")).not.toBe(providerModelsEntry(api, "mimo"))
    let scope = "host-a"
    const hosted = { discoverModels: api.discoverModels, discoveryScope: () => scope }
    const onA = providerModelsEntry(hosted, "qwen")
    scope = "host-b"
    expect(providerModelsEntry(hosted, "qwen")).not.toBe(onA)
    scope = "host-a"
    expect(providerModelsEntry(hosted, "qwen")).toBe(onA)
    const throwing = {
      discoveryScope: () => {
        throw new Error("no pinia")
      },
    }
    expect(providerModelsEntry(throwing, "qwen")).toBe(providerModelsEntry(throwing, "qwen"))
  })

  it("treats a clock that moved backwards as stale", async () => {
    const api = catalogApi()
    const first = loadProviderModels(api, "qwen")
    api.pending[0].resolve({ models: "not a list" })
    expect(await first).toEqual([])
    vi.setSystemTime(new Date("2026-11-26T09:00:00Z"))
    expect(isProviderModelsStale(providerModelsEntry(api, "qwen"))).toBe(true)
  })
})
