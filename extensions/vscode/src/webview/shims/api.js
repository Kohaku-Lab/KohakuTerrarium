export const terrariumAPI = {
  getHistory: (graph, target) => globalThis.__ktVsCodeHistory(graph, target),
  getHistoryPage: (graph, target, options = {}) => globalThis.__ktVsCodeHistoryPage(graph, target, options),
  getHistoryDetail: (graph, target, params = {}) => globalThis.__ktVsCodeHistoryDetail(graph, target, params),
  interruptCreature: (graph, target) => globalThis.__ktVsCodeInterrupt(graph, target),
  executeCreatureCommand: async (graph, target, command, args = '') => {
    if (command !== 'goal' || typeof args !== 'string') throw Error('Only goal commands are supported')
    if (typeof globalThis.__ktVsCodeGoal !== 'function') throw Error('Goal command bridge is unavailable')
    return globalThis.__ktVsCodeGoal(graph, target, args)
  },
  sendToChannel: async () => {},
  promoteCreatureTask: async () => {},
}

// The shared chat store imports ``sessionAPI`` for saved (v1) session history,
// but this live-only webview has no Host route for it. Fail loudly instead of
// resolving ``undefined`` so a future saved-viewer wiring cannot regress to a
// silent no-op.
function unsupportedSavedHistory(operation) {
  return () => {
    throw Error(`Saved-session ${operation} is unavailable in the VS Code view`)
  }
}

export const sessionAPI = {
  getHistoryPage: unsupportedSavedHistory('history paging'),
  getHistoryDetail: unsupportedSavedHistory('history detail'),
}

export const agentAPI = {
  regenerate: async () => {},
  editMessage: async () => {},
  rewindTo: async () => {},
}
