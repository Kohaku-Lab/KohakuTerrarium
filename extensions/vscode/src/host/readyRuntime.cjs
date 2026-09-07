function beginReady(host, readyId) {
  if (host.disposed) throw Error('Runtime ownership changed')
  if (host.runtimeEpoch === readyId) return
  host.runtimeEpoch = readyId
  host.selectionIntentVersion++
  host.topologyReconcileVersion++
  host.rotateGeneration()
  for (const controller of host.readyControllers) controller.abort()
  for (const controller of host.topologyControllers) controller.abort()
  for (const cancel of host.pendingGoals) cancel(Error('Ready ownership changed; execution outcome may be unknown'))
}

async function reconcileReady(host, readyId) {
  const controller = new AbortController()
  const intent = host.selectionIntentVersion
  host.readyControllers.add(controller)
  const owns = () => !host.disposed && !controller.signal.aborted && host.runtimeEpoch === readyId && host.selectionIntentVersion === intent
  const assertCurrent = () => {
    if (!owns()) throw Error('Ready ownership changed')
  }
  let timer
  const cancelled = new Promise((_, reject) => {
    controller.signal.addEventListener('abort', () => reject(Error('Ready ownership changed')), { once: true })
    timer = setTimeout(() => {
      reject(Error('Ready reconciliation timed out'))
      controller.abort()
    }, host.topologyTimeoutMs)
  })
  try {
    return await Promise.race([
      host.enqueueSelectionOperation(async () => {
        assertCurrent()
        const sessions = await Promise.race([host.client.listOpen({ signal: controller.signal }), cancelled])
        assertCurrent()
        const current = host.state.selection
        if (!current?.targetCreatureId) return { selection: null, changed: false, selectionVersion: host.selectionVersion }
        const result = host.reconciledSelection(current, sessions)
        if (result.changed) {
          const applied = await host.state.updateSelectionIf(result.selection, owns)
          if (!applied) {
            assertCurrent()
            throw Error('Ready selection ownership changed')
          }
          host.rotateGeneration()
          host.selectionVersion++
        }
        assertCurrent()
        return { ...result, selectionVersion: host.selectionVersion }
      }),
      cancelled,
    ])
  } catch (error) {
    if (!host.disposed && !controller.signal.aborted && host.runtimeEpoch === readyId && host.selectionIntentVersion !== intent)
      return host.supersededTopologySelection()
    throw error
  } finally {
    clearTimeout(timer)
    host.readyControllers.delete(controller)
    controller.abort()
  }
}

module.exports = { beginReady, reconcileReady }
