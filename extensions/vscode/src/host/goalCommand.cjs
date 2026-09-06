const { allowedMessage } = require('./protocol.cjs')

async function executeGoal(host, message) {
  if (!allowedMessage(message)) throw Error('Invalid goal command envelope')
  const selected = host.state.selection
  const intent = host.selectionIntentVersion
  const controller = new AbortController()
  const owns = () =>
    !controller.signal.aborted &&
    !host.disposed &&
    selected?.targetCreatureId &&
    selected === host.state.selection &&
    message.readyId === host.runtimeEpoch &&
    message.selectionVersion === host.selectionVersion &&
    intent === host.selectionIntentVersion &&
    host.pendingSelectionMutations === 0
  if (!owns()) throw Error('Selected Creature ownership changed')
  let rejectCancelled
  const cancelled = new Promise((_, reject) => {
    rejectCancelled = reject
  })
  const cancel = (error) => {
    rejectCancelled(error)
    controller.abort()
  }
  host.pendingGoals.add(cancel)
  const timer = setTimeout(() => cancel(Error('Goal command timed out; execution outcome may be unknown')), host.goalTimeoutMs)
  try {
    return await Promise.race([
      host.enqueueSelectionOperation(() => {
        if (!owns()) throw Error('Selected Creature ownership changed')
        return Promise.race([
          host.client.creatureCommand(selected.session, selected.targetCreatureId, 'goal', message.args, { signal: controller.signal }),
          cancelled,
        ])
      }),
      cancelled,
    ])
  } finally {
    clearTimeout(timer)
    host.pendingGoals.delete(cancel)
    controller.abort()
  }
}

module.exports = { executeGoal }
