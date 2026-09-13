import { settleRequestMessage } from './requestDemux.mjs'

const isBranchRequest = (type) => type === 'http.editMessage' || type === 'http.regenerate'

// Tracks host requests and timers; timeoutMs: 0 waits without a deadline.
export function createRequestLifecycle({
  postMessage,
  defaultTimeoutMs = 30000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  firstRequestId = 1000,
}) {
  const pending = new Map()
  let nextRequestId = firstRequestId

  function request(type, data = {}, onSend = () => {}, { timeoutMs = defaultTimeoutMs } = {}) {
    return new Promise((resolve, reject) => {
      const id = nextRequestId++
      onSend(id)
      const timer =
        timeoutMs > 0
          ? setTimer(() => {
              pending.delete(id)
              reject(Error('KohakuTerrarium request timed out'))
            }, timeoutMs)
          : null
      pending.set(id, { resolve, reject, timer, type })
      try {
        postMessage({ type, requestId: id, ...data })
      } catch (error) {
        pending.delete(id)
        if (timer !== null) clearTimer(timer)
        if (isBranchRequest(type)) error.mayHaveRun = false
        reject(error)
      }
    })
  }

  function rejectAll(error) {
    for (const entry of pending.values()) {
      if (entry.timer !== null) clearTimer(entry.timer)
      const failure = isBranchRequest(entry.type) ? Object.assign(Error(error.message), { mayHaveRun: true, superseded: true }) : error
      entry.reject(failure)
    }
    pending.clear()
  }

  function settle(message) {
    return settleRequestMessage(pending, message, clearTimer)
  }

  return { request, rejectAll, settle, size: () => pending.size }
}
