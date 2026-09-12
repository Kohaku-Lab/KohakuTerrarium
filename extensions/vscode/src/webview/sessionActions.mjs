// Session lifecycle actions for the webview shell. Extracted from the app entry
// so the component stays focused on wiring; behavior is unchanged — each action
// owns its own busy/error brackets and refreshes the saved-session list.
export function createSessionActions({ shell, currentSession, busy, error, reloadSessions }) {
  async function createSession() {
    busy.value = true
    error.value = ''
    try {
      currentSession.value = await shell.create()
      await reloadSessions()
    } catch (cause) {
      error.value = cause.message
    } finally {
      busy.value = false
    }
  }

  async function resumeSession(session) {
    busy.value = true
    error.value = ''
    try {
      currentSession.value = await shell.resume(session.savedName)
      await reloadSessions()
    } catch (cause) {
      error.value = cause.message
    } finally {
      busy.value = false
    }
  }

  async function stopSession() {
    if (!currentSession.value?.targetCreatureId) return
    busy.value = true
    error.value = ''
    try {
      await shell.stop(currentSession.value)
      currentSession.value = null
      await reloadSessions()
    } catch (cause) {
      error.value = cause.message
    } finally {
      busy.value = false
    }
  }

  async function openSession(session, creatureId) {
    busy.value = true
    error.value = ''
    try {
      currentSession.value = await shell.open(session, creatureId)
    } catch (cause) {
      error.value = cause.message
    } finally {
      busy.value = false
    }
  }

  return { createSession, resumeSession, stopSession, openSession }
}

// A target change reuses the real Session-open workflow (select + rebind), fenced
// so a superseded reply cannot reopen a stale socket or clobber the newest target.
export function createTargetSelector({ shell, currentSession, error, getReadyId, getOperationEpoch }) {
  let epoch = 0
  return async function selectTarget(name) {
    const session = currentSession.value?.session
    if (!session) return
    const creature = (session.creatures || []).find((candidate) => candidate.name === name)
    if (!creature) return
    const ownedEpoch = ++epoch
    const operation = getOperationEpoch()
    const readyId = getReadyId()
    const runtimeId = session.runtimeId
    const isCurrent = () =>
      ownedEpoch === epoch &&
      operation === getOperationEpoch() &&
      readyId === getReadyId() &&
      currentSession.value?.session?.runtimeId === runtimeId
    error.value = ''
    try {
      const restored = await shell.open(session, creature.id, { isCurrent })
      if (restored && isCurrent()) currentSession.value = restored
    } catch (cause) {
      if (isCurrent()) error.value = cause?.message || String(cause)
    }
  }
}
