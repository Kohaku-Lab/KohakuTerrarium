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
  getSubagentConversation: subagentDelegate('__ktVsCodeSubagentConversation'),
  sendSubagentMessage: subagentDelegate('__ktVsCodeSubagentSend'),
  promoteCreatureTask: subagentDelegate('__ktVsCodePromote'),
  getCreatureCommandInventory: modelDelegate('__ktVsCodeCommandInventory', 'Command inventory'),
  switchCreatureModel: modelDelegate('__ktVsCodeSwitchModel', 'Model switch'),
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

// Saved sub-agent discovery/conversation are read-only delegates; there is no
// saved send. Each named delegate maps to exactly one fixed Host route.
export const sessionAPI = {
  getHistoryPage: unsupportedSavedHistory('history paging'),
  getHistoryDetail: unsupportedSavedHistory('history detail'),
  listSubagents: subagentDelegate('__ktVsCodeSubagentList'),
  getSubagentConversation: subagentDelegate('__ktVsCodeSavedSubagentConversation'),
  getActive: modelDelegate('__ktVsCodeInstanceMetadata', 'Instance metadata'),
}

export const agentAPI = {
  regenerate: async () => {},
  editMessage: async () => {},
  rewindTo: async () => {},
}

// Model/slash + instance-metadata facade (the M vertical). The model directory and
// the live command inventory are read-only; only ``switchCreatureModel`` mutates,
// and each named delegate maps to exactly one fixed Host route installed by
// ``modelBridge``. A missing delegate rejects explicitly (never a silent no-op).
// ``executeCreatureCommand`` above intentionally stays goal-only: a readable
// inventory is NOT authorization to dispatch other slash commands.
export const configAPI = {
  getModels: modelDelegate('__ktVsCodeModelDirectory', 'Model directory'),
}

// Resolve one Host facade installed by ``subagentBridge`` at webview setup. A
// missing delegate rejects explicitly (never a silent no-op) and the Host's safe
// HTTP status is presented in the ``error.response.status`` shape the shared
// sub-agent leaf reads to tell a 409 conflict from a plain failure. No backend
// body is synthesised; only the status the Host already forwarded crosses over.
function subagentDelegate(name) {
  return (...args) =>
    Promise.resolve()
      .then(() => {
        const delegate = globalThis[name]
        if (typeof delegate !== 'function') throw Error('Sub-agent bridge is unavailable')
        return delegate(...args)
      })
      .catch((error) => {
        if (error && Number.isSafeInteger(error.status) && error.response == null) error.response = { status: error.status }
        throw error
      })
}

// Resolve one Host facade installed by ``modelBridge`` at webview setup. Same
// fail-explicit + safe-status contract as ``subagentDelegate``, kept separate so
// each vertical names the Host route it depends on.
function modelDelegate(name, label) {
  return (...args) =>
    Promise.resolve()
      .then(() => {
        const delegate = globalThis[name]
        if (typeof delegate !== 'function') throw Error(`${label} bridge is unavailable`)
        return delegate(...args)
      })
      .catch((error) => {
        if (error && Number.isSafeInteger(error.status) && error.response == null) error.response = { status: error.status }
        throw error
      })
}
