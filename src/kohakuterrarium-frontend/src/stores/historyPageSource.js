import { mergeRawRecords } from "./historyProjection"

export function createHistoryPageSource({
  fetchPage,
  fetchDetail,
  getSourceKey,
  getInstanceGeneration,
  getMutationGeneration,
  pageSize = 400,
  onResetRequired,
}) {
  let generation = 0
  let headSequence = 0
  let pendingHead = null
  let inFlight = 0
  let window = null
  let suffix = []
  let metadata = null
  let suffixFence = null
  let resetRequired = false
  const cache = new Map()
  const pendingOlder = new Map()

  function capture() {
    return [generation, getSourceKey(), getInstanceGeneration(), getMutationGeneration()]
  }
  function isCurrent(fence, includeMutation = true) {
    return (
      !!fence &&
      capture().every((value, index) => (!includeMutation && index === 3) || value === fence[index])
    )
  }
  function reset() {
    generation += 1
    headSequence += 1
    pendingHead = null
    window = null
    resetRequired = false
    suffix = []
    metadata = null
    suffixFence = null
    cache.clear()
    pendingOlder.clear()
    return { discarded: false }
  }
  function invalidate() {
    reset()
    onResetRequired?.()
    resetRequired = true
    return { discarded: true, resetRequired: true }
  }
  async function request(options) {
    const fence = capture()
    const fetchedAt = Date.now()
    inFlight += 1
    try {
      const payload = await fetchPage({ limit: pageSize, ...options })
      if (!isCurrent(fence)) return { discarded: true, resetRequired: false }
      const page = payload?.history_page
      if (!page) return { discarded: false, legacy: true, payload, fetchedAt, fence }
      if (page.reset_required) return invalidate()
      if (window && (page.history_id !== window.historyId || page.stream !== window.stream)) {
        return invalidate()
      }
      const records = page.stream === "events" ? payload.events : payload.messages
      return {
        discarded: false,
        records: Array.isArray(records) ? records : [],
        page,
        payload,
        fetchedAt,
        fence,
      }
    } finally {
      inFlight -= 1
    }
  }
  function setWindow(page) {
    window = {
      before: page.before ?? null,
      after: page.after ?? null,
      hasOlder: !!page.has_older,
      hasNewer: !!page.has_newer,
      historyId: page.history_id ?? null,
      stream: page.stream,
      resetRequired: false,
    }
  }
  function applied() {
    return { discarded: false, applied: true, records: [...suffix], ...metadata }
  }
  const source = {
    getWindow: () => (window ? { ...window } : null),
    getRecords: () => [...suffix],
    isCurrent: () => isCurrent(suffixFence),
    getState: () => ({
      pending: inFlight > 0,
      resetRequired,
      sourceKey: getSourceKey(),
      hasOlder: !!window?.hasOlder,
      hasNewer: !!window?.hasNewer,
      hasCachedOlder: cache.has(window?.before),
    }),
    async initialize(options = {}) {
      reset()
      const sequence = headSequence
      let result = await request(options)
      if (result.discarded || sequence !== headSequence)
        return { discarded: true, resetRequired: result.resetRequired }
      if (result.legacy) return result
      if (
        result.page.stream === "events" &&
        !result.records.length &&
        !result.page.has_older &&
        result.page.before == null &&
        options.stream == null
      ) {
        result = await request({ stream: "snapshot" })
        if (result.discarded || sequence !== headSequence)
          return { discarded: true, resetRequired: result.resetRequired }
        if (result.legacy) return result
      }
      suffix = result.records
      suffixFence = result.fence
      metadata = { payload: result.payload, fetchedAt: result.fetchedAt }
      setWindow(result.page)
      return applied()
    },
    async prefetchOlder() {
      if (window && isCurrent(suffixFence, false) && !isCurrent(suffixFence)) {
        const refreshed = await source.refreshHead()
        if (refreshed.discarded) return refreshed
      }
      const before = window?.before
      if (before == null || !window.hasOlder || !isCurrent(suffixFence))
        return { discarded: false, cached: false }
      if (cache.has(before)) return { discarded: false, cached: true, boundary: before }
      if (pendingOlder.has(before)) return pendingOlder.get(before)
      const promise = (async () => {
        const result = await request({
          before,
          history_id: window.historyId,
          stream: window.stream,
        })
        if (result.discarded) return result
        if (result.legacy) return invalidate()
        cache.set(before, result)
        return { discarded: false, cached: true, boundary: before }
      })()
      pendingOlder.set(before, promise)
      try {
        return await promise
      } finally {
        if (pendingOlder.get(before) === promise) pendingOlder.delete(before)
      }
    },
    materializeCachedOlder(beforeApply) {
      const cached = cache.get(window?.before)
      if (!cached || window?.hasNewer || !isCurrent(cached.fence, false) || !isCurrent(suffixFence))
        return { discarded: true, merged: false }
      beforeApply?.()
      cache.delete(window.before)
      suffix = mergeRawRecords(cached.records, suffix)
      window.before = cached.page.before
      window.hasOlder = !!cached.page.has_older
      return { ...applied(), merged: true, before: window.before, hasOlder: window.hasOlder }
    },
    async ensureOlder() {
      if (cache.has(window?.before)) {
        const result = source.materializeCachedOlder()
        return { ...result, action: "materialized", resetRequired: false }
      }
      const result = await source.prefetchOlder()
      return {
        ...result,
        action: result.cached ? "prefetched" : "none",
        resetRequired: !!result.resetRequired,
      }
    },
    async refreshHead() {
      if (pendingHead) return pendingHead
      if (!window || !isCurrent(suffixFence, false)) return { discarded: true, applied: false }
      const sequence = ++headSequence
      const fence = capture()
      const promise = (async () => {
        let after = window.after
        let records = []
        let result
        for (let pageCount = 0; pageCount < 32; pageCount++) {
          result = await request({
            ...(after != null ? { after } : {}),
            history_id: window.historyId,
            stream: window.stream,
          })
          if (result.discarded || sequence !== headSequence || !isCurrent(fence))
            return { discarded: true, resetRequired: result.resetRequired }
          if (result.legacy) return invalidate()
          records = mergeRawRecords(records, result.records)
          const next = result.page.after ?? after
          if (!result.page.has_newer) {
            after = next
            break
          }
          if (next === after) return invalidate()
          after = next
        }
        suffix = mergeRawRecords(suffix, records)
        suffixFence = result.fence
        window.after = after
        window.hasNewer = !!result.page.has_newer
        metadata = { payload: result.payload, fetchedAt: result.fetchedAt }
        return applied()
      })()
      pendingHead = promise
      try {
        return await promise
      } finally {
        if (pendingHead === promise) pendingHead = null
      }
    },
    async loadRecord(key) {
      const record = suffix.find((item) => item._history_key === key)
      if (!record?._history_truncated || !record._history_detail || !isCurrent(suffixFence))
        return { discarded: true }
      const fence = capture()
      const { stream, historyId } = window
      const detail = await fetchDetail({
        stream,
        history_id: historyId,
        ref: record._history_detail,
      })
      if (!isCurrent(fence) || !isCurrent(suffixFence)) return { discarded: true }
      if (
        detail.history_page?.history_id !== historyId ||
        detail.history_page?.stream !== stream ||
        detail.record?._history_key !== key
      )
        return { discarded: true }
      suffix = suffix.map((item) => (item._history_key === key ? detail.record : item))
      return applied()
    },
    reset,
  }
  return source
}
