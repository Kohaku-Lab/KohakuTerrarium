import { flushPromises } from "@vue/test-utils"
import { effectScope, nextTick, ref } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { PROVIDER_MODELS_TTL_MS, _resetProviderModelsForTests } from "@/utils/providerModels"

import { useSwitcherCatalog } from "./useSwitcherCatalog"

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function setup(names = ["mimo", "qwen"]) {
  const pending = []
  const api = {
    getBackends: vi.fn(async () => ({
      backends: names.map((name) => ({ name, model_discovery: true, available: true })),
    })),
    discoverModels: vi.fn(() => {
      const next = deferred()
      pending.push(next)
      return next.promise
    }),
  }
  const open = ref(false)
  const provider = ref(names[0])
  const scope = effectScope()
  const catalog = scope.run(() => useSwitcherCatalog(api, open, provider))
  return { api, pending, open, provider, catalog, scope }
}

const rows = (...ids) => ids.map((id) => ({ id, name: id }))
const ids = (catalog) => catalog.rows.value.map((row) => row.id)

beforeEach(() => {
  _resetProviderModelsForTests()
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(new Date("2026-11-26T10:00:00Z"))
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useSwitcherCatalog", () => {
  it("shows cached rows at once on provider switch and reopen, loading only when nothing is cached", async () => {
    const { api, pending, open, provider, catalog, scope } = setup()
    open.value = true
    await flushPromises()
    expect(catalog.busy.value).toBe(true)
    pending[0].resolve({ models: rows("m1"), fetched_at: "2026-11-26T10:00:00+00:00" })
    await flushPromises()
    expect(ids(catalog)).toEqual(["m1"])
    expect(catalog.fetchedAt.value).toBe("2026-11-26T10:00:00+00:00")

    provider.value = "qwen"
    expect(catalog.busy.value).toBe(true)
    pending[1].resolve({ models: rows("q1") })
    await flushPromises()
    provider.value = "mimo"
    expect(ids(catalog)).toEqual(["m1"])
    expect(catalog.busy.value).toBe(false)

    open.value = false
    expect(ids(catalog)).toEqual([])
    open.value = true
    expect(catalog.busy.value).toBe(false)
    await flushPromises()
    expect(ids(catalog)).toEqual(["m1"])
    expect(catalog.busy.value).toBe(false)
    expect(api.discoverModels.mock.calls).toEqual([["mimo"], ["qwen"]])
    scope.stop()
  })

  it("revalidates a stale list in the background after the TTL", async () => {
    const { api, pending, open, catalog, scope } = setup()
    open.value = true
    await flushPromises()
    pending[0].resolve({ models: rows("old") })
    await flushPromises()
    open.value = false
    vi.setSystemTime(Date.now() + PROVIDER_MODELS_TTL_MS)
    open.value = true
    await flushPromises()
    expect(api.discoverModels.mock.calls).toEqual([["mimo"], ["mimo"]])
    expect(ids(catalog)).toEqual(["old"])
    expect(catalog.busy.value).toBe(false)
    pending[1].resolve({ models: rows("old", "new") })
    await flushPromises()
    expect(ids(catalog)).toEqual(["old", "new"])
    scope.stop()
  })

  it("forces refresh=true on Refresh, keeps rows and selection, and surfaces a failure without clearing", async () => {
    const { api, pending, open, provider, catalog, scope } = setup()
    open.value = true
    await flushPromises()
    pending[0].resolve({ models: rows("a", "b") })
    await flushPromises()
    catalog.selectedId.value = "b"

    catalog.refresh()
    expect(api.discoverModels.mock.calls).toEqual([["mimo"], ["mimo", { refresh: true }]])
    expect(ids(catalog)).toEqual(["a", "b"])
    expect(catalog.busy.value).toBe(false)
    pending[1].reject({ response: { data: { detail: "Provider is offline" } } })
    await flushPromises()
    expect(ids(catalog)).toEqual(["a", "b"])
    expect(catalog.error.value).toBe("Provider is offline")
    expect(catalog.selected.value?.id).toBe("b")

    catalog.refresh()
    pending[2].resolve({ models: rows("a") })
    await flushPromises()
    await nextTick()
    expect(catalog.error.value).toBe("")
    expect(catalog.selectedId.value).toBe("")

    catalog.selectedId.value = "a"
    provider.value = "qwen"
    expect(catalog.selectedId.value).toBe("")
    pending[3].reject(new Error("offline"))
    await flushPromises()
    expect(catalog.error.value).toBe("Could not load provider models.")
    scope.stop()
  })

  it("does not fetch while the saved-presets tab is shown and retries providers on Refresh", async () => {
    const { api, pending, open, catalog, scope } = setup()
    api.getBackends.mockRejectedValueOnce({ response: { data: { detail: "Host down" } } })
    open.value = true
    await flushPromises()
    expect(catalog.providersError.value).toBe("Host down")
    expect(api.discoverModels).not.toHaveBeenCalled()
    catalog.refresh()
    await flushPromises()
    expect(catalog.providersError.value).toBe("")
    expect(api.discoverModels.mock.calls).toEqual([["mimo"]])
    pending[0].resolve({ models: rows("a") })
    await flushPromises()
    catalog.showSaved.value = true
    expect(catalog.visible.value).toBe(false)
    expect(ids(catalog)).toEqual([])
    catalog.showSaved.value = false
    expect(ids(catalog)).toEqual(["a"])
    expect(api.discoverModels).toHaveBeenCalledTimes(1)
    scope.stop()
  })
})
