export const terrariumAPI = {
  getHistory: (graph, target) => globalThis.__ktVsCodeHistory(graph, target),
  interruptCreature: (graph, target) => globalThis.__ktVsCodeInterrupt(graph, target),
  executeCreatureCommand: async (graph, target, command, args = '') => {
    if (command !== 'goal' || typeof args !== 'string') throw Error('Only goal commands are supported')
    if (typeof globalThis.__ktVsCodeGoal !== 'function') throw Error('Goal command bridge is unavailable')
    return globalThis.__ktVsCodeGoal(graph, target, args)
  },
  sendToChannel: async () => {},
  promoteCreatureTask: async () => {},
}

export const agentAPI = {
  regenerate: async () => {},
  editMessage: async () => {},
  rewindTo: async () => {},
}
