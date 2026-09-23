import { ref } from "vue"

import { formatDateTime } from "./usageFormat"

const CLEARING = new Set(["not_logged_in", "auth_expired", "unsupported", "no_data", "no_data_yet"])
const TRANSIENT = new Set(["unavailable"])

export function useProviderUsage(load, { t, fallbackKey }) {
  const usage = ref(null)
  const loading = ref(false)
  const initial = ref(false)
  const error = ref("")
  const stale = ref(false)
  const staleAt = ref("")
  const node = ref("")
  let generation = 0

  function invalidate(nextNode = node.value) {
    generation += 1
    node.value = nextNode
    usage.value = null
    error.value = ""
    stale.value = false
    staleAt.value = ""
    loading.value = false
    initial.value = false
  }

  function markStale(snapshot) {
    stale.value = true
    staleAt.value = formatDateTime(snapshot?.captured_at) || t("settings.account.grok.unknown")
  }

  function retain(snapshot, { failed = false } = {}) {
    usage.value = snapshot
    if (failed || snapshot?.source === "cache") markStale(snapshot)
    else {
      stale.value = false
      staleAt.value = ""
    }
  }

  async function refresh(nextNode = node.value) {
    const requestNode = nextNode
    const requestGeneration = ++generation
    node.value = requestNode
    const previous = usage.value?.status === "ok" ? usage.value : null
    loading.value = true
    if (!previous) initial.value = true
    error.value = ""
    try {
      const data = await load(requestNode)
      if (requestGeneration !== generation) return
      if (data?.status && data.status !== "ok") {
        if (TRANSIENT.has(data.status) && previous) {
          error.value = t(fallbackKey)
          retain(previous, { failed: true })
          return
        }
        usage.value = CLEARING.has(data.status) || TRANSIENT.has(data.status) ? data : null
        if (!usage.value) error.value = t(fallbackKey)
        stale.value = false
        staleAt.value = ""
        return
      }
      retain(data?.status === "ok" ? data : null)
      if (!usage.value) error.value = t(fallbackKey)
    } catch (err) {
      if (requestGeneration !== generation) return
      error.value = t(fallbackKey)
      const status = err?.response?.status
      if (previous && status !== 401 && status !== 403 && status !== 404)
        retain(previous, { failed: true })
      else {
        usage.value = null
        stale.value = false
        staleAt.value = ""
      }
    } finally {
      if (requestGeneration === generation) {
        loading.value = false
        initial.value = false
      }
    }
  }

  return { usage, loading, initial, error, stale, staleAt, node, invalidate, refresh }
}
