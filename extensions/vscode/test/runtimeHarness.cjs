const { RuntimeHost } = require('../src/host/runtime.cjs')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function harness(options = {}) {
  const updates = []
  const posts = []
  const socketCalls = []
  const client = {
    createdPayload: null,
    listOpen: async () => [],
    resume: async () => ({
      instance_id: 'graph-resumed',
      type: 'agent',
      session_name: 'saved-one',
      session: {
        session_id: 'graph-resumed',
        name: 'saved-one',
        creatures: [{ creature_id: 'creature-resumed', name: 'resumed' }],
      },
    }),
    stop: async () => ({ status: 'stopped' }),
    async createCreature(payload) {
      this.createdPayload = payload
      return {
        session_id: 'graph-created',
        type: 'creature',
        config_name: 'swe',
        creatures: [{ creature_id: 'creature-created', name: 'swe' }],
      }
    },
    active: async () => ({
      session_id: 'graph-live',
      type: 'terrarium',
      config_name: 'team',
      creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
    }),
    history: async () => ({ events: [] }),
    interrupt: async () => ({ ok: true }),
    commandCalls: [],
    async creatureCommand(session, creature, command, args) {
      this.commandCalls.push({ session, creature, command, args })
      return { ok: true }
    },
  }
  const state = {
    selection: null,
    async updateSelection(selection) {
      this.selection = selection
      updates.push(selection)
    },
    async updateSelectionIf(selection, owns) {
      if (!owns()) return false
      this.selection = selection
      updates.push(selection)
      return true
    },
  }
  const sockets = {
    beginCount: 0,
    begin() {
      this.beginCount++
      return this.beginCount
    },
    open(generation, socketId, factory) {
      socketCalls.push({ generation, socketId, socket: factory() })
    },
    send() {
      return true
    },
    closeSocket() {
      return true
    },
    closeGeneration() {},
  }
  const host = new RuntimeHost({
    client,
    state,
    sockets,
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt-biome/creatures/swe',
    getWorkspacePath: () => 'C:/workspace',
    socketFactory: (url, protocols) => ({ url, protocols }),
    webSocketBase: 'ws://127.0.0.1:8000',
    token: 'host-secret',
    runtimeEpoch: 'ready-B',
    topologyTimeoutMs: options.topologyTimeoutMs,
  })
  return { client, host, posts, socketCalls, sockets, state, updates }
}

module.exports = { deferred, harness }
