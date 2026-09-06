function chatInstance(session) {
  if (!session.runtimeId) throw Error('Live Session has no runtime')
  return {
    id: session.runtimeId,
    graph_id: session.runtimeId,
    session_id: session.runtimeId,
    type: session.kind,
    config_name: session.title,
    creatures: session.creatures.map((creature) => ({
      id: creature.id,
      creature_id: creature.id,
      name: creature.name,
      is_root: false,
    })),
    channels: [],
  }
}

export function createSessionShell({ api, chat }) {
  async function attach(session, creatureId) {
    const selected = session.creatures.find((creature) => creature.id === creatureId)
    if (!selected?.id) throw Error('Unknown Creature target')
    const instance = chatInstance(session)
    const ownership = await api.select({
      session: session.runtimeId,
      creatureId: selected.id,
    })
    chat.unbindFromInstance()
    chat.initForInstance(instance, { initialTab: selected.name, autoReconnect: false })
    return {
      session,
      instance,
      target: selected.name,
      targetCreatureId: ownership.targetCreatureId,
    }
  }

  async function attachWhenSingle(session) {
    if (session.creatures.length === 1) return attach(session, session.creatures[0].id)
    await api.clearSelection()
    chat.unbindFromInstance()
    return { session, target: null, targetCreatureId: null }
  }

  return {
    list: () => api.list(),
    restore: (session, selection) => {
      const creature = session.creatures.find((candidate) => candidate.id === selection.targetCreatureId)
      if (!creature || session.runtimeId !== selection.session) {
        throw Error('Reconciled Creature is not in Sessions')
      }
      const instance = chatInstance(session)
      chat.unbindFromInstance()
      chat.initForInstance(instance, { initialTab: creature.name, autoReconnect: false })
      return {
        session,
        instance,
        target: creature.name,
        targetCreatureId: creature.id,
      }
    },
    create: async () => attachWhenSingle(await api.create()),
    resume: async (savedName) => attachWhenSingle(await api.resume(savedName)),
    stop: async (current) => {
      await api.stop({
        session: current.session.runtimeId,
        creatureId: current.targetCreatureId,
      })
      chat.unbindFromInstance()
    },
    open: (session, creatureId) => attach(session, creatureId),
  }
}
