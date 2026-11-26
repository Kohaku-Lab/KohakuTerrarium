import { ref, shallowRef } from "vue"

// Shared stale-while-revalidate cache of provider model catalogs, used by the
// model switcher and the Settings discovery picker. Host-neutral: entries are
// keyed by the catalog API object, its optional `discoveryScope()`, and the
// provider name.
export const PROVIDER_MODELS_TTL_MS = 10 * 60_000

let caches = new WeakMap()
let globalEpoch = 0
const providerEpochs = new Map()

function epochOf(provider) {
  return `${globalEpoch}:${providerEpochs.get(provider) || 0}`
}

function scopeOf(api) {
  try {
    return api?.discoveryScope?.() || ""
  } catch {
    return ""
  }
}

function createEntry(epoch) {
  return {
    epoch,
    rows: shallowRef([]),
    fetchedAt: ref(""),
    hasLoaded: ref(false),
    loading: ref(false),
    failed: ref(false),
    errorDetail: ref(""),
    loadedAt: 0,
    inFlight: null,
    forced: false,
    request: 0,
  }
}

/** The live cache entry for one provider of `api`; replaced after invalidation. */
export function providerModelsEntry(api, provider) {
  let entries = caches.get(api)
  if (!entries) {
    entries = new Map()
    caches.set(api, entries)
  }
  const key = `${scopeOf(api)}\u0000${provider}`
  const epoch = epochOf(provider)
  let entry = entries.get(key)
  if (!entry || entry.epoch !== epoch) {
    entry = createEntry(epoch)
    entries.set(key, entry)
  }
  return entry
}

export function isProviderModelsStale(entry, now = Date.now()) {
  if (!entry.hasLoaded.value) return true
  const age = now - entry.loadedAt
  return age < 0 || age >= PROVIDER_MODELS_TTL_MS
}

/**
 * Fetch `provider`'s catalog into its entry when missing or older than the TTL,
 * or always with `refresh`. Concurrent calls share one request; a failure keeps
 * the previous rows and marks the entry failed. Resolves to the entry's rows.
 */
export function loadProviderModels(api, provider, { refresh = false } = {}) {
  const entry = providerModelsEntry(api, provider)
  if (entry.inFlight && (!refresh || entry.forced)) return entry.inFlight
  if (!refresh && !isProviderModelsStale(entry)) return Promise.resolve(entry.rows.value)
  const request = ++entry.request
  entry.forced = refresh
  entry.loading.value = true
  let call
  try {
    call = refresh ? api.discoverModels(provider, { refresh: true }) : api.discoverModels(provider)
  } catch (cause) {
    call = Promise.reject(cause)
  }
  const flight = Promise.resolve(call)
    .then(
      (data) => {
        if (request !== entry.request) return entry.rows.value
        entry.rows.value = Array.isArray(data?.models) ? data.models : []
        entry.fetchedAt.value = data?.fetched_at || ""
        entry.loadedAt = Date.now()
        entry.hasLoaded.value = true
        entry.failed.value = false
        entry.errorDetail.value = ""
        return entry.rows.value
      },
      (cause) => {
        if (request === entry.request) {
          entry.failed.value = true
          entry.errorDetail.value = cause?.response?.data?.detail || ""
        }
        return entry.rows.value
      },
    )
    .finally(() => {
      if (request !== entry.request) return
      entry.loading.value = false
      entry.inFlight = null
      entry.forced = false
    })
  entry.inFlight = flight
  return flight
}

/** Forget `provider`'s cached catalog on every host, or every provider's. */
export function invalidateProviderModels(provider) {
  if (provider) providerEpochs.set(provider, (providerEpochs.get(provider) || 0) + 1)
  else globalEpoch++
}

export function _resetProviderModelsForTests() {
  caches = new WeakMap()
  providerEpochs.clear()
  globalEpoch = 0
}
