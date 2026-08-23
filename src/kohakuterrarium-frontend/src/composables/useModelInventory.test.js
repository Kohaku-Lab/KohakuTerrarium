import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  MODEL_INVENTORY_FRESH_MS,
  _resetModelInventoryForTests,
  useModelInventory,
} from "@/composables/useModelInventory"
import { useHostsStore } from "@/stores/hosts"
import { configAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  configAPI: {
    getModels: vi.fn(),
  },
}))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useRealTimers()
  vi.resetAllMocks()
  _resetModelInventoryForTests()
})

describe("useModelInventory", () => {
  it("loads the model inventory once and exposes the result to later consumers", async () => {
    const models = [{ name: "fast", provider: "codex", available: true }]
    configAPI.getModels.mockResolvedValueOnce(models)

    const first = useModelInventory()
    await first.ensureLoaded()
    const second = useModelInventory()
    await second.ensureLoaded()

    expect(configAPI.getModels).toHaveBeenCalledOnce()
    expect(first.models.value).toEqual(models)
    expect(second.models.value).toEqual(models)
    expect(second.initialLoading.value).toBe(false)
  })

  it("keeps separate app-lifetime inventories for different hosts", async () => {
    const hosts = useHostsStore()
    hosts.hosts = [
      { id: "host-a", name: "A", url: "http://a.test" },
      { id: "host-b", name: "B", url: "http://b.test" },
    ]
    configAPI.getModels.mockResolvedValueOnce([{ name: "same-origin" }])
    const sameOrigin = useModelInventory()
    await sameOrigin.ensureLoaded()

    hosts.activeHostId = "host-a"
    configAPI.getModels.mockResolvedValueOnce([{ name: "remote-a" }])
    const remoteA = useModelInventory()
    await remoteA.ensureLoaded()

    hosts.activeHostId = null
    expect(useModelInventory().models.value).toEqual([{ name: "same-origin" }])
    hosts.activeHostId = "host-a"
    expect(useModelInventory().models.value).toEqual([{ name: "remote-a" }])
    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
  })

  it("loads the newly active host for an existing consumer", async () => {
    const hosts = useHostsStore()
    hosts.hosts = [{ id: "host-a", name: "A", url: "http://a.test" }]
    configAPI.getModels
      .mockResolvedValueOnce([{ name: "same-origin" }])
      .mockResolvedValueOnce([{ name: "remote-a" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    hosts.activeHostId = "host-a"
    await Promise.resolve()
    await Promise.resolve()

    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
    expect(inventory.models.value).toEqual([{ name: "remote-a" }])
  })

  it("discards an in-flight response after switching hosts", async () => {
    const hosts = useHostsStore()
    hosts.hosts = [{ id: "host-a", name: "A", url: "http://a.test" }]
    const oldRequest = deferred()
    configAPI.getModels
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValueOnce([{ name: "remote-a" }])

    const inventory = useModelInventory()
    const initialLoad = inventory.ensureLoaded()
    hosts.activeHostId = "host-a"
    await Promise.resolve()
    await Promise.resolve()
    oldRequest.resolve([{ name: "wrong-host" }])
    await initialLoad

    hosts.activeHostId = null
    expect(inventory.models.value).toEqual([])
    expect(inventory.hasLoaded.value).toBe(false)
    hosts.activeHostId = "host-a"
    expect(inventory.models.value).toEqual([{ name: "remote-a" }])
  })

  it("shares an in-flight initial request between concurrent consumers", async () => {
    const request = deferred()
    configAPI.getModels.mockReturnValueOnce(request.promise)

    const first = useModelInventory()
    const second = useModelInventory()
    const firstLoad = first.ensureLoaded()
    const secondLoad = second.ensureLoaded()

    expect(configAPI.getModels).toHaveBeenCalledOnce()
    expect(first.initialLoading.value).toBe(true)
    request.resolve([{ name: "shared" }])
    await Promise.all([firstLoad, secondLoad])

    expect(second.models.value).toEqual([{ name: "shared" }])
    expect(first.initialLoading.value).toBe(false)
  })

  it("treats an empty response as a successfully loaded inventory", async () => {
    configAPI.getModels.mockResolvedValueOnce([])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    await inventory.ensureLoaded()

    expect(inventory.hasLoaded.value).toBe(true)
    expect(inventory.models.value).toEqual([])
    expect(configAPI.getModels).toHaveBeenCalledOnce()
  })

  it("allows a failed initial load to be retried", async () => {
    configAPI.getModels
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([{ name: "retry" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    await inventory.ensureLoaded()

    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
    expect(inventory.hasLoaded.value).toBe(true)
    expect(inventory.models.value).toEqual([{ name: "retry" }])
  })

  it("keeps cached models visible while a stale inventory revalidates", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"))
    configAPI.getModels.mockResolvedValueOnce([{ name: "old" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    vi.advanceTimersByTime(MODEL_INVENTORY_FRESH_MS + 1)
    const request = deferred()
    configAPI.getModels.mockReturnValueOnce(request.promise)

    const refresh = inventory.revalidateIfStale()

    expect(inventory.models.value).toEqual([{ name: "old" }])
    expect(inventory.initialLoading.value).toBe(false)
    expect(inventory.refreshing.value).toBe(true)
    request.resolve([{ name: "new" }])
    await refresh
    expect(inventory.models.value).toEqual([{ name: "new" }])
  })

  it("revalidates after an inventory that was previously fresh becomes stale", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"))
    configAPI.getModels
      .mockResolvedValueOnce([{ name: "old" }])
      .mockResolvedValueOnce([{ name: "new" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    await inventory.revalidateIfStale()
    vi.advanceTimersByTime(MODEL_INVENTORY_FRESH_MS + 1)
    await inventory.revalidateIfStale()

    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
    expect(inventory.models.value).toEqual([{ name: "new" }])
  })

  it("does not revalidate a fresh inventory", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"))
    configAPI.getModels.mockResolvedValueOnce([{ name: "fresh" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    vi.advanceTimersByTime(MODEL_INVENTORY_FRESH_MS - 1)
    await inventory.revalidateIfStale()

    expect(configAPI.getModels).toHaveBeenCalledOnce()
  })

  it("forces a refresh while retaining stale models if the request fails", async () => {
    configAPI.getModels
      .mockResolvedValueOnce([{ name: "old" }])
      .mockRejectedValueOnce(new Error("failed"))

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    await inventory.refresh()

    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
    expect(inventory.models.value).toEqual([{ name: "old" }])
    expect(inventory.hasLoaded.value).toBe(true)
    expect(inventory.refreshing.value).toBe(false)
    expect(inventory.error.value).toBe("failed")
  })

  it("deduplicates an explicit refresh against an in-flight revalidation", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"))
    configAPI.getModels.mockResolvedValueOnce([{ name: "old" }])

    const inventory = useModelInventory()
    await inventory.ensureLoaded()
    vi.advanceTimersByTime(MODEL_INVENTORY_FRESH_MS + 1)
    const request = deferred()
    configAPI.getModels.mockReturnValueOnce(request.promise)

    const staleRefresh = inventory.revalidateIfStale()
    const forcedRefresh = inventory.refresh()

    expect(configAPI.getModels).toHaveBeenCalledTimes(2)
    request.resolve([{ name: "new" }])
    await Promise.all([staleRefresh, forcedRefresh])
    expect(inventory.models.value).toEqual([{ name: "new" }])
  })
})
