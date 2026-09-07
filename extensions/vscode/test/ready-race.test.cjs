const assert = require('node:assert/strict')
const test = require('node:test')
const Module = require('node:module')
const path = require('node:path')
const { RuntimeHost } = require('../src/host/runtime.cjs')
const { SocketOwners } = require('../src/host/sockets.cjs')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

test('webview ready reconciliation only applies the latest overlapping result', async () => {
  const { createReadyCoordinator } = await import('../src/webview/readyCoordinator.mjs')
  const requests = []
  const state = { available: false, automatic: true, selection: null, sessions: [], runtime: null }
  const coordinator = createReadyCoordinator({
    requestReady() {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
    async applyReady(result, isCurrent) {
      const listed = await result.list
      if (!isCurrent()) return
      Object.assign(state, result.connection, { sessions: listed })
    },
  })

  const older = coordinator.reconcile()
  const newer = coordinator.reconcile()
  const newerList = deferred()
  requests[1].resolve({
    connection: { available: true, automatic: false, selection: 'new', runtime: 'new-runtime' },
    list: newerList.promise,
  })
  newerList.resolve(['new-session'])
  await newer

  requests[0].resolve({
    connection: { available: true, automatic: true, selection: 'old', runtime: 'old-runtime' },
    list: Promise.resolve(['old-session']),
  })
  await older

  assert.deepEqual(state, {
    available: true,
    automatic: false,
    selection: 'new',
    sessions: ['new-session'],
    runtime: 'new-runtime',
  })
})

test('webview ignores an older ready failure after a newer success', async () => {
  const { createReadyCoordinator } = await import('../src/webview/readyCoordinator.mjs')
  const requests = []
  const state = { available: false, sessions: [], runtime: null, error: '' }
  const coordinator = createReadyCoordinator({
    requestReady() {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
    async applyReady(result, isCurrent) {
      if (isCurrent()) Object.assign(state, result)
    },
    async applyFailure(error, isCurrent) {
      if (isCurrent()) Object.assign(state, { available: false, sessions: [], runtime: null, error: error.message })
    },
  })

  const older = coordinator.reconcile()
  const newer = coordinator.reconcile()
  requests[1].resolve({ available: true, sessions: ['new-session'], runtime: 'new-runtime', error: '' })
  await newer
  requests[0].reject(Error('old failure'))
  await older

  assert.deepEqual(state, { available: true, sessions: ['new-session'], runtime: 'new-runtime', error: '' })
})

for (const staleOutcome of ['resolve', 'reject']) {
  test(`host retains replacement runtime promise after stale ${staleOutcome}`, async () => {
    const builds = []
    const posted = []
    const commands = new Map()
    let provider
    let receiveMessage
    let disposeView
    const vscode = {
      commands: {
        registerCommand(name, callback) {
          commands.set(name, callback)
          return { dispose() {} }
        },
        async executeCommand() {},
      },
      window: {
        registerWebviewViewProvider(_name, value) {
          provider = value
          return { dispose() {} }
        },
        async showInformationMessage() {},
      },
      workspace: { getConfiguration: () => ({ get: () => '' }) },
      Uri: { joinPath: (...parts) => parts.join('/') },
    }
    class StateWriter {
      constructor() {
        this.value = { manual: false, selection: null }
      }
      read() {
        return this.value
      }
      async update(change) {
        const next = change(this.value)
        if (!next) return { applied: false, value: this.value }
        this.value = next
        return { applied: true, value: this.value }
      }
    }
    const runtimes = []
    let clientCount = 0
    let topologyStarts = 0
    let topologyCloses = 0
    let listCount = 0
    const client = {
      listOpen: async () => {
        listCount++
        return []
      },
    }
    class ObservedRuntime extends RuntimeHost {
      constructor(options) {
        super(options)
        runtimes.push(this)
      }
    }
    class TopologyWatcher {
      start() {
        topologyStarts++
      }
      close() {
        topologyCloses++
      }
    }
    const originalLoad = Module._load
    Module._load = function (request, parent, isMain) {
      const mocks = {
        vscode,
        ws: class {},
        './host/connection.cjs': { resolveLocalConnection: ({ discover }) => discover() },
        './host/localDiscovery.cjs': {
          discoverInstalledKt() {
            const build = deferred()
            builds.push(build)
            return build.promise
          },
          probeCapabilities: async () => ({}),
        },
        './host/client.cjs': {
          createClient: () => {
            clientCount++
            return client
          },
          validateCapabilities: (value) => value,
        },
        './host/runtime.cjs': { RuntimeHost: ObservedRuntime },
        './host/sockets.cjs': { SocketOwners },
        './host/state.cjs': { ConnectionStateWriter: StateWriter },
        './host/topology.cjs': { TopologyWatcher },
      }
      return Object.hasOwn(mocks, request) ? mocks[request] : originalLoad(request, parent, isMain)
    }
    const extensionPath = path.resolve(__dirname, '../src/extension.cjs')
    delete require.cache[extensionPath]
    let extension
    try {
      extension = require(extensionPath)
    } finally {
      Module._load = originalLoad
    }
    const context = {
      subscriptions: [],
      workspaceState: {},
      secrets: { async get() {}, async store() {} },
      extensionUri: 'extension',
    }
    extension.activate(context)
    const webview = {
      cspSource: 'test',
      asWebviewUri: String,
      onDidReceiveMessage(callback) {
        receiveMessage = callback
        return { dispose() {} }
      },
      async postMessage(message) {
        posted.push(message)
      },
    }
    provider.resolveWebviewView({
      webview,
      onDidDispose(callback) {
        disposeView = callback
      },
    })

    const stale = receiveMessage({ type: 'ready', requestId: 1 })
    assert.equal(builds.length, 1)
    await commands.get('kohakuterrarium.useAutomaticDiscovery')()
    const replacement = receiveMessage({ type: 'ready', requestId: 2 })
    assert.equal(builds.length, 2)

    if (staleOutcome === 'resolve') {
      builds[0].resolve({ endpoint: 'http://127.0.0.1:8000', token: '', source: 'automatic' })
    } else {
      builds[0].reject(Error('stale failure'))
    }
    await stale

    const shared = receiveMessage({ type: 'ready', requestId: 3 })
    assert.equal(builds.length, 2, 'the pending replacement build remains the shared promise')
    builds[1].resolve({ endpoint: 'http://127.0.0.1:8001', token: '', source: 'automatic' })
    await Promise.all([replacement, shared])
    const connectionId = posted.find((message) => message.type === 'ready.result' && message.data.available)?.data.connectionId
    assert.equal(typeof connectionId, 'string')
    assert.doesNotMatch(connectionId, /127\.0\.0\.1|8001|http/)
    const counts = { clientCount, topologyStarts, topologyCloses, listCount }
    const retained = runtimes.at(-1)
    const refreshed = receiveMessage({ type: 'ready', requestId: 4 })
    assert.equal(builds.length, 2, 'healthy Refresh reuses the established connection')
    await refreshed
    assert.equal(posted.at(-1).data.connectionId, connectionId)
    assert.equal(posted.at(-1).data.readyId, 4)
    assert.equal(runtimes.at(-1), retained)
    assert.equal(clientCount, counts.clientCount)
    assert.equal(topologyStarts, counts.topologyStarts)
    assert.equal(topologyCloses, counts.topologyCloses)
    assert.equal(listCount, counts.listCount + 1)
    await commands.get('kohakuterrarium.useAutomaticDiscovery')()
    const switched = receiveMessage({ type: 'ready', requestId: 5 })
    builds[2].resolve({ endpoint: 'http://127.0.0.1:8002', token: '', source: 'automatic' })
    await switched
    assert.notEqual(posted.at(-1).data.connectionId, connectionId)
    const live = runtimes.at(-1)
    const blocked = deferred()
    client.listOpen = () => blocked.promise
    const interrupted = receiveMessage({ type: 'ready', requestId: 6 })
    await new Promise((resolve) => setImmediate(resolve))
    const selectIntent = receiveMessage({ type: 'session.clearSelection', requestId: 7, readyId: 6 })
    blocked.resolve([])
    await Promise.all([interrupted, selectIntent])
    assert.equal(live.disposed, false, 'superseding selection does not destroy a healthy runtime')
    assert.equal(builds.length, 3)
    client.listOpen = () => new Promise(() => {})
    live.topologyTimeoutMs = 5
    await receiveMessage({ type: 'ready', requestId: 8 })
    assert.equal(live.disposed, true)
    assert.equal(builds.length, 3, 'failed Refresh does not rediscover or retry')
    await receiveMessage({ type: 'session.list', requestId: 9 })
    assert.equal(builds.length, 3, 'ordinary stale requests cannot discover after failure')
    client.listOpen = async () => []
    const retry = receiveMessage({ type: 'ready', requestId: 10 })
    assert.equal(builds.length, 4)
    builds[3].resolve({ endpoint: 'http://127.0.0.1:8002', token: '', source: 'automatic' })
    await retry
    assert.equal(posted.at(-1).data.readyId, 10)
    disposeView()
  })
}

test('host connection ownership prevents superseded failure or success from replacing runtime', async () => {
  const { createConnectionAttemptOwner } = require('../src/host/connectionAttempt.cjs')
  const owner = createConnectionAttemptOwner()
  let runtime = null
  const older = owner.begin()
  const newer = owner.begin()

  if (newer.isCurrent()) runtime = 'new-runtime'
  if (older.isCurrent()) runtime = 'old-runtime'
  assert.equal(runtime, 'new-runtime')

  if (older.isCurrent()) runtime = null
  assert.equal(runtime, 'new-runtime')

  owner.invalidate()
  assert.equal(newer.isCurrent(), false)
})
