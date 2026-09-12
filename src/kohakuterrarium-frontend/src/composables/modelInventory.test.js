import { effectScope, nextTick, ref } from "vue"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  _resetModelInventoryForTests,
  createModelInventory,
  MODEL_INVENTORY_FRESH_MS,
} from "@/composables/modelInventory"

function deferred() {
  let resolve
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

beforeEach(() => {
  _resetModelInventoryForTests()
  vi.useRealTimers()
})

describe("createModelInventory", () => {
  it.each(["epoch-0", "epoch-2"])(
    "discards intermediate epochs even when a same-tick burst ends at %s",
    async (finalKey) => {
      const scope = effectScope()
      const hostKey = ref("epoch-0")
      let revision = 0
      const inventory = scope.run(() =>
        createModelInventory({
          getHostKey: () => hostKey.value,
          retainPreviousHosts: false,
          fetchModels: async (key) => [{ name: `${key}:${revision}` }],
        }),
      )
      try {
        await inventory.ensureLoaded()
        hostKey.value = "epoch-1"
        const intermediate = inventory.ensureLoaded()
        hostKey.value = finalKey
        await nextTick()
        await intermediate
        await inventory.ensureLoaded()
        revision++
        hostKey.value = "epoch-1"
        await nextTick()
        await inventory.ensureLoaded()
        expect(inventory.models.value).toEqual([{ name: "epoch-1:1" }])
      } finally {
        scope.stop()
      }
    },
  )

  it("releases previous epochs when the host opts out of cross-host retention", async () => {
    const scope = effectScope()
    const hostKey = ref("epoch-0")
    let revision = 0
    const inventory = scope.run(() =>
      createModelInventory({
        getHostKey: () => hostKey.value,
        retainPreviousHosts: false,
        fetchModels: async (key) => [{ name: `${key}:${revision}` }],
      }),
    )
    try {
      await inventory.ensureLoaded()
      expect(inventory.models.value).toEqual([{ name: "epoch-0:0" }])
      for (revision = 1; revision <= 5; revision++) {
        hostKey.value = `epoch-${revision}`
        await nextTick()
        await inventory.ensureLoaded()
        expect(inventory.models.value).toEqual([{ name: `epoch-${revision}:${revision}` }])
      }
      hostKey.value = "epoch-0"
      await nextTick()
      await inventory.ensureLoaded()
      expect(inventory.models.value).toEqual([{ name: "epoch-0:6" }])
    } finally {
      scope.stop()
    }
  })

  it("keeps transient view inventories separate from retained host inventories", async () => {
    const shared = createModelInventory({
      getHostKey: () => "same-key",
      fetchModels: async () => [{ name: "dashboard" }],
    })
    await shared.ensureLoaded()
    const scope = effectScope()
    const transient = scope.run(() =>
      createModelInventory({
        getHostKey: () => "same-key",
        retainPreviousHosts: false,
        fetchModels: async () => [{ name: "webview" }],
      }),
    )
    try {
      await transient.ensureLoaded()
      expect(transient.models.value).toEqual([{ name: "webview" }])
      expect(shared.models.value).toEqual([{ name: "dashboard" }])
    } finally {
      scope.stop()
    }
    expect(shared.models.value).toEqual([{ name: "dashboard" }])
  })

  it("disposes a transient inventory without admitting late results or new reads", async () => {
    const scope = effectScope()
    const pending = deferred()
    const inventory = scope.run(() =>
      createModelInventory({
        getHostKey: () => "view-epoch",
        retainPreviousHosts: false,
        fetchModels: () => pending.promise,
      }),
    )
    const first = inventory.ensureLoaded()
    scope.stop()
    pending.resolve([{ name: "late-model" }])
    await first
    expect(inventory.models.value).toEqual([])
    expect(inventory.initialLoading.value).toBe(false)
    await expect(inventory.ensureLoaded()).rejects.toThrow(/disposed/i)
    await expect(inventory.refresh()).rejects.toThrow(/disposed/i)
    await expect(inventory.revalidateIfStale()).rejects.toThrow(/disposed/i)
  })

  it("keys the cache on the explicit host key and never leaks one host's models", async () => {
    const hostKey = ref("host-a")
    const fetchModels = vi.fn(async () =>
      hostKey.value === "host-a" ? [{ name: "a" }] : [{ name: "b" }],
    )
    const inventory = createModelInventory({ getHostKey: () => hostKey.value, fetchModels })

    await inventory.ensureLoaded()
    expect(inventory.models.value).toEqual([{ name: "a" }])

    hostKey.value = "host-b"
    await Promise.resolve()
    await Promise.resolve()
    expect(fetchModels).toHaveBeenCalledTimes(2)
    expect(inventory.models.value).toEqual([{ name: "b" }])
  })

  it("discards an in-flight directory response after the host key changes", async () => {
    const hostKey = ref("host-a")
    const pending = deferred()
    const fetchModels = vi.fn((key) =>
      key === "host-a" ? pending.promise : Promise.resolve([{ name: "new-host" }]),
    )
    const inventory = createModelInventory({ getHostKey: () => hostKey.value, fetchModels })

    const first = inventory.ensureLoaded()
    hostKey.value = "host-b"
    await Promise.resolve()
    await Promise.resolve()
    pending.resolve([{ name: "old-host" }])
    await first

    // The late host-a response must not surface as host-b's models.
    expect(inventory.models.value).toEqual([{ name: "new-host" }])
  })

  it("keeps cached models visible while a stale inventory revalidates", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"))
    const fetchModels = vi.fn().mockResolvedValueOnce([{ name: "old" }])
    const inventory = createModelInventory({ getHostKey: () => "host-a", fetchModels })
    await inventory.ensureLoaded()
    vi.advanceTimersByTime(MODEL_INVENTORY_FRESH_MS + 1)
    fetchModels.mockResolvedValueOnce([{ name: "new" }])

    const refresh = inventory.revalidateIfStale()
    expect(inventory.models.value).toEqual([{ name: "old" }])
    expect(inventory.refreshing.value).toBe(true)
    await refresh
    expect(inventory.models.value).toEqual([{ name: "new" }])
  })

  it("presents a failed refresh truthfully without discarding the cached models", async () => {
    const fetchModels = vi
      .fn()
      .mockResolvedValueOnce([{ name: "old" }])
      .mockRejectedValueOnce(new Error("refresh failed"))
    const inventory = createModelInventory({ getHostKey: () => "host-a", fetchModels })
    await inventory.ensureLoaded()
    await inventory.refresh()

    expect(inventory.models.value).toEqual([{ name: "old" }])
    expect(inventory.error.value).toBe("refresh failed")
  })
})
