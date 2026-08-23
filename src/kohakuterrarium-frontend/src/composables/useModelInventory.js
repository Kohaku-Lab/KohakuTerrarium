import { computed, readonly, ref, watch } from "vue"

import { useHostsStore } from "@/stores/hosts"
import { configAPI } from "@/utils/api"

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

function bucketFor(key) {
  let bucket = buckets.get(key)
  if (!bucket) {
    bucket = createBucket()
    buckets.set(key, bucket)
  }
  return bucket
}

async function load(key) {
  const bucket = bucketFor(key)
  if (bucket.inFlight) return bucket.inFlight
  bucket.initialLoading.value = !bucket.hasLoaded.value
  bucket.refreshing.value = bucket.hasLoaded.value
  bucket.error.value = ""
  const requestId = ++bucket.requestId
  bucket.inFlight = configAPI
    .getModels()
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

export function useModelInventory() {
  const hosts = useHostsStore()
  const key = computed(() => hosts.activeHostId || "_same_origin")
  const current = () => bucketFor(key.value)
  const models = computed(() => current().models.value)
  const hasLoaded = computed(() => current().hasLoaded.value)
  const initialLoading = computed(() => current().initialLoading.value)
  const refreshing = computed(() => current().refreshing.value)
  const error = computed(() => current().error.value)

  watch(key, (nextKey, previousKey) => {
    const previous = bucketFor(previousKey)
    if (previous.inFlight) {
      previous.requestId++
      previous.inFlight = null
      previous.initialLoading.value = false
      previous.refreshing.value = false
    }
    if (!bucketFor(nextKey).hasLoaded.value) load(nextKey)
  })

  return {
    models: readonly(models),
    hasLoaded: readonly(hasLoaded),
    initialLoading: readonly(initialLoading),
    refreshing: readonly(refreshing),
    error: readonly(error),
    ensureLoaded() {
      const bucket = current()
      return bucket.hasLoaded.value ? Promise.resolve(bucket.models.value) : load(key.value)
    },
    revalidateIfStale() {
      const bucket = current()
      const isFresh =
        bucket.hasLoaded.value && Date.now() - bucket.fetchedAt < MODEL_INVENTORY_FRESH_MS
      return isFresh ? Promise.resolve(bucket.models.value) : load(key.value)
    },
    refresh() {
      return load(key.value)
    },
  }
}

export function _resetModelInventoryForTests() {
  buckets.clear()
}
