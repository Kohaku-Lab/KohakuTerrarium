// Installs lifecycle-fenced edit/regenerate delegates with no rerun deadline.
export function installBranchBridge({ request, getOwner }) {
  let disposed = false

  // An explicit null admittedReadyId rejects calls during reconciliation.
  function readyIdOf(owner) {
    return owner?.admittedReadyId !== undefined ? owner.admittedReadyId : owner?.readyId
  }

  function options(data) {
    return {
      ...(data.turnIndex != null ? { turnIndex: data.turnIndex } : {}),
      ...(data.userPosition != null ? { userPosition: data.userPosition } : {}),
      ...(data.branchView && Object.keys(data.branchView).length ? { branchView: { ...data.branchView } } : {}),
      ...(data.requestId ? { correlationId: data.requestId } : {}),
      ...(data.locator ? { locator: { ...data.locator } } : {}),
    }
  }

  async function send(type, payload) {
    if (disposed) throw Object.assign(Error('Branch bridge is disposed'), { mayHaveRun: false, superseded: true })
    const owner = { ...getOwner?.() }
    const readyId = readyIdOf(owner)
    if (!Number.isSafeInteger(readyId) || readyId < 1)
      throw Object.assign(Error('Wait for Session refresh'), { mayHaveRun: false, superseded: true })
    const isCurrent = () => {
      const current = getOwner?.()
      return (
        !disposed && readyIdOf(current) === readyId && current?.runtimeId === owner.runtimeId && current?.creatureId === owner.creatureId
      )
    }
    try {
      const result = await request(type, { ...payload, readyId }, () => {}, { timeoutMs: 0 })
      if (!isCurrent()) throw Object.assign(Error('Branch request ownership changed'), { mayHaveRun: true, superseded: true })
      return result
    } catch (error) {
      if (!isCurrent()) error.superseded = true
      throw error
    }
  }

  const regenerate = (session, creature, target = {}) => send('http.regenerate', { session, creature, ...options(target) })

  const editMessage = (session, creature, msgIdx, content, target = {}) =>
    send('http.editMessage', { session, creature, msgIdx, content, ...options(target) })

  globalThis.__ktVsCodeRegenerate = regenerate
  globalThis.__ktVsCodeEditMessage = editMessage

  return () => {
    disposed = true
    if (globalThis.__ktVsCodeRegenerate === regenerate) delete globalThis.__ktVsCodeRegenerate
    if (globalThis.__ktVsCodeEditMessage === editMessage) delete globalThis.__ktVsCodeEditMessage
  }
}
