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
  function apply(result, canApply) {
    if (canApply && !canApply()) return { applied: false }
    if (result.discarded) return { applied: false, resetRequired: result.resetRequired }
    if (!result.applied && !result.legacy) return { applied: false }
    if (source.getWindow()?.hasNewer) return { applied: false, catchingUp: true }
    if (applyReplay(result.records, result) === false) return { applied: false, deferred: true }
    return { applied: true, legacy: !!result.legacy, payload: result.payload }
  }
  async function run(method, argument, canApply) {
    const promise = source[method](argument)
    publish()
    try {
      return apply(await promise, canApply)
    } finally {
      publish()
    }
  }
  return {
    kind,
    getState,
    isCurrent: source.isCurrent,
    initialize: (initialPayload, canApply) => run("initialize", { initialPayload }, canApply),
    refreshHead: (canApply) => run("refreshHead", undefined, canApply),
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
