import { computed, getCurrentScope, onScopeDispose, readonly, ref, watch } from "vue"

import { configAPI } from "@/utils/api"

// Freshness window for the host-keyed model directory.
export const MODEL_INVENTORY_FRESH_MS = 60_000

const buckets = new Map()

function createBucket() {
  return {
    models: ref([]),
    hasLoaded: ref(false),
    initialLoading: ref(false),
    refreshing: ref(false),
    error: ref(""),
    fetchedAt: 0,
    inFlight: null,
    requestId: 0,
  }
}

function bucketFor(key, cache) {
  let bucket = cache.get(key)
  if (!bucket) {
    bucket = createBucket()
    cache.set(key, bucket)
  }
  return bucket
}

async function load(key, fetchModels, cache) {
  const bucket = bucketFor(key, cache)
  if (bucket.inFlight) return bucket.inFlight
  bucket.initialLoading.value = !bucket.hasLoaded.value
  bucket.refreshing.value = bucket.hasLoaded.value
  bucket.error.value = ""
  const requestId = ++bucket.requestId
  bucket.inFlight = fetchModels(key)
    .then((data) => {
      if (requestId !== bucket.requestId) return bucket.models.value
      bucket.models.value = Array.isArray(data) ? data : []
      bucket.hasLoaded.value = true
      bucket.fetchedAt = Date.now()
      return bucket.models.value
    })
    .catch((err) => {
      if (requestId === bucket.requestId) bucket.error.value = err?.message || String(err)
      return bucket.models.value
    })
    .finally(() => {
      if (requestId !== bucket.requestId) return
      bucket.initialLoading.value = false
      bucket.refreshing.value = false
      bucket.inFlight = null
    })
  return bucket.inFlight
}

export function createModelInventory({
  getHostKey,
  fetchModels = () => configAPI.getModels(),
  retainPreviousHosts = true,
}) {
  const cache = retainPreviousHosts ? buckets : new Map()
  const disposed = ref(false)
  const empty = createBucket()
  const key = computed(() => getHostKey() || "_default")
  const current = () => (disposed.value ? empty : bucketFor(key.value, cache))
  if (!retainPreviousHosts && getCurrentScope()) {
    onScopeDispose(() => {
      disposed.value = true
      for (const bucket of cache.values()) {
        bucket.requestId++
        bucket.models.value = []
        bucket.inFlight = null
      }
      cache.clear()
    })
  }
  const models = computed(() => current().models.value)
  const hasLoaded = computed(() => current().hasLoaded.value)
  const initialLoading = computed(() => current().initialLoading.value)
  const refreshing = computed(() => current().refreshing.value)
  const error = computed(() => current().error.value)

  watch(
    key,
    (nextKey, previousKey) => {
      const previous = bucketFor(previousKey, cache)
      if (previous.inFlight) {
        previous.requestId++
        previous.inFlight = null
        previous.initialLoading.value = false
        previous.refreshing.value = false
      }
      if (!retainPreviousHosts) {
        previous.models.value = []
        cache.delete(previousKey)
      }
      if (!bucketFor(nextKey, cache).hasLoaded.value) load(nextKey, fetchModels, cache)
    },
    { flush: retainPreviousHosts ? "pre" : "sync" },
  )

  return {
    models: readonly(models),
    hasLoaded: readonly(hasLoaded),
    initialLoading: readonly(initialLoading),
    refreshing: readonly(refreshing),
    error: readonly(error),
    ensureLoaded() {
      if (disposed.value) return Promise.reject(new Error("Model inventory is disposed"))
      const bucket = current()
      return bucket.hasLoaded.value
        ? Promise.resolve(bucket.models.value)
        : load(key.value, fetchModels, cache)
    },
    revalidateIfStale() {
      if (disposed.value) return Promise.reject(new Error("Model inventory is disposed"))
      const bucket = current()
      const isFresh =
        bucket.hasLoaded.value && Date.now() - bucket.fetchedAt < MODEL_INVENTORY_FRESH_MS
      return isFresh ? Promise.resolve(bucket.models.value) : load(key.value, fetchModels, cache)
    },
    refresh() {
      if (disposed.value) return Promise.reject(new Error("Model inventory is disposed"))
      return load(key.value, fetchModels, cache)
    },
  }
}

export function _resetModelInventoryForTests() {
  buckets.clear()
}
