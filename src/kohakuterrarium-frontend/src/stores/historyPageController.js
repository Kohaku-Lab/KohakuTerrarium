import { createHistoryPageSource } from "./historyPageSource"

export function createHistoryPageController({ kind = "live", applyReplay, onChange, ...options }) {
  const source = createHistoryPageSource(options)
  function getState() {
    const window = source.getWindow()
    return {
      ...source.getState(),
      historyId: window?.historyId ?? null,
      stream: window?.stream ?? null,
    }
  }
  function publish() {
    onChange?.(getState())
  }
  function apply(result) {
    if (result.discarded) return { applied: false, resetRequired: result.resetRequired }
    if (!result.applied && !result.legacy) return { applied: false }
    if (source.getWindow()?.hasNewer) return { applied: false, catchingUp: true }
    applyReplay(result.records, result)
    return { applied: true, legacy: !!result.legacy, payload: result.payload }
  }
  async function run(method, argument) {
    const promise = source[method](argument)
    publish()
    try {
      return apply(await promise)
    } finally {
      publish()
    }
  }
  return {
    kind,
    getState,
    isCurrent: source.isCurrent,
    initialize: () => run("initialize"),
    refreshHead: () => run("refreshHead"),
    loadRecord: (key) => run("loadRecord", key),
    async prefetchOlder() {
      const promise = source.prefetchOlder()
      publish()
      try {
        return await promise
      } finally {
        publish()
      }
    },
    materializeOlder(beforeApply) {
      const result = source.materializeCachedOlder(beforeApply)
      const out = result.merged ? apply(result) : { applied: false }
      publish()
      return out
    },
    reset() {
      source.reset()
      publish()
    },
    dispose() {
      source.reset()
    },
  }
}
