export function installGoalBridge({ chat, request, ownership, getTarget, getFence, isReconciling = () => false }) {
  const execute = async (graph, target, args) => {
    const owned = getTarget()
    if (!owned || graph !== owned.runtimeId || target !== owned.name) throw Error('Goal target ownership changed')
    const context = chat.registerCommandResultContext(chat.activeTab)
    const tab = chat.activeTab
    const commandText = `/goal${args ? ` ${args}` : ''}`
    const isCurrent = () => ownership.isCurrent(owned)
    let surfaced = false
    try {
      const result = await ownership.dispatch(async (assertCurrent) => {
        if (isReconciling()) throw Error('Session is reconciling; wait before sending a goal command')
        const fence = await getFence()
        if (!fence) throw Error('Wait for the current Session to reconnect')
        assertCurrent()
        return request('goal.execute', { args, ...fence })
      })
      if (!result || typeof result.success !== 'boolean') throw Error('Invalid goal command result')
      if (isCurrent()) {
        chat.addCommandResult(tab, commandText, result, context)
        surfaced = true
      }
      if (!result.success || result.error) throw Error(result.error || result.output || 'Goal command failed')
      return result
    } finally {
      if (!surfaced) chat.releaseCommandResultContext(tab, context)
    }
  }
  globalThis.__ktVsCodeGoal = execute
  return () => {
    if (globalThis.__ktVsCodeGoal === execute) delete globalThis.__ktVsCodeGoal
  }
}
