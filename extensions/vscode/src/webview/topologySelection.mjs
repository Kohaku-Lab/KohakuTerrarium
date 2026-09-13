export async function applyTopologySelection({
  selection,
  changed = true,
  shell,
  chat,
  getCurrentSession,
  setCurrentSession,
  setSessions,
  isCurrent = () => true,
}) {
  if (!isCurrent()) return
  let listed
  try {
    listed = await shell.list()
  } catch (error) {
    if (!isCurrent()) return
    throw error
  }
  if (!isCurrent()) return
  setSessions(listed)
  if (!selection) {
    if (!isCurrent()) return
    chat.unbindFromInstance()
    if (!isCurrent()) return
    setCurrentSession(null)
    return
  }
  if (!isCurrent()) return
  const currentSession = getCurrentSession()
  if (!changed && currentSession?.targetCreatureId === selection.targetCreatureId) {
    const session = listed.find(
      (candidate) =>
        candidate.isLive &&
        candidate.runtimeId === selection.session &&
        candidate.creatures.some((creature) => creature.id === selection.targetCreatureId),
    )
    if (session && isCurrent()) setCurrentSession({ ...currentSession, session })
    return
  }
  const session = listed.find(
    (candidate) =>
      candidate.isLive &&
      candidate.runtimeId === selection.session &&
      candidate.creatures.some((creature) => creature.id === selection.targetCreatureId),
  )
  if (!isCurrent()) return
  if (!session) {
    chat.unbindFromInstance()
    if (!isCurrent()) return
    setCurrentSession(null)
    if (!isCurrent()) return
    throw Error('Selected Creature is no longer open')
  }
  const restored = shell.restore(session, selection)
  if (!isCurrent()) return
  setCurrentSession(restored)
}
