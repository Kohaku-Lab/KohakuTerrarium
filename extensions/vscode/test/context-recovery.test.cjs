const assert = require('node:assert/strict')
const Module = require('node:module')
const test = require('node:test')

const localDiscovery = require('../src/host/localDiscovery.cjs')

function loadExtension(vscode, overrides = {}) {
  const filename = require.resolve('../src/extension.cjs')
  const originalLoad = Module._load
  delete require.cache[filename]
  Module._load = function (request, parent, isMain) {
    if (request === 'vscode') return vscode
    if (request === './host/localDiscovery.cjs') return { ...localDiscovery, ...overrides }
    return originalLoad(request, parent, isMain)
  }
  try {
    return require(filename)
  } finally {
    Module._load = originalLoad
    delete require.cache[filename]
  }
}

function fakeVscode() {
  const captures = { provider: null }
  const vscode = {
    window: {
      registerWebviewViewProvider(id, provider) {
        captures.provider = provider
        return { dispose() {} }
      },
      showQuickPick: async () => undefined,
      showInputBox: async () => undefined,
      showWarningMessage: async () => undefined,
      showInformationMessage: async () => undefined,
      showErrorMessage: async () => undefined,
    },
    commands: { registerCommand: () => ({ dispose() {} }) },
    Uri: { joinPath: (base, ...parts) => ({ fsPath: [base?.fsPath ?? base, ...parts].join('/') }) },
    workspace: { getConfiguration: () => ({ get: (_key, fallback) => fallback }), workspaceFolders: [] },
    env: { language: 'en' },
  }
  return { vscode, captures }
}

function fakeContext() {
  const store = new Map()
  return {
    extensionUri: { fsPath: 'C:/ext' },
    subscriptions: { push() {} },
    secrets: { get: async (key) => store.get(key), store: async (key, value) => store.set(key, value) },
    workspaceState: { get: (key) => store.get(key), update: async (key, value) => store.set(key, value) },
  }
}

function fakeView() {
  const posts = []
  let receiver = null
  const webview = {
    cspSource: 'vscode-resource://ext',
    options: {},
    html: '',
    asWebviewUri: (uri) => uri,
    postMessage: (message) => {
      posts.push(message)
    },
    onDidReceiveMessage: (callback) => {
      receiver = callback
      return { dispose() {} }
    },
  }
  const view = { webview, onDidDispose: () => ({ dispose() {} }) }
  return { view, posts, receive: (message) => receiver(message) }
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

// C1: context management is a user command. A disconnected view must fail clearly against
// the current-runtime admission contract instead of silently rediscovering a connection;
// only an explicit ready (Refresh) may build a runtime.

test('a disconnected context action fails clearly without attempting rediscovery', async () => {
  let discoverCalls = 0
  const { vscode, captures } = fakeVscode()
  const extension = loadExtension(vscode, {
    discoverInstalledKt: async () => {
      discoverCalls++
      throw Error('discovery must not run for a user context command')
    },
  })
  extension.activate(fakeContext())
  const { view, posts, receive } = fakeView()
  captures.provider.resolveWebviewView(view)

  await receive({ type: 'context.compact', requestId: 1 })
  await flush()

  assert.equal(discoverCalls, 0)
  const errors = posts.filter((post) => post.type === 'error')
  assert.equal(errors.length, 1)
  assert.equal(errors[0].requestId, 1)
  assert.equal(errors[0].code, 'context_command_failed')
  assert.match(errors[0].error, /Refresh the Session/i)
  assert.equal(
    posts.some((post) => post.type === 'context.compact.result'),
    false,
  )
})

test('only an explicit ready may attempt rediscovery', async () => {
  let discoverCalls = 0
  const { vscode, captures } = fakeVscode()
  const extension = loadExtension(vscode, {
    discoverInstalledKt: async () => {
      discoverCalls++
      throw Error('no local service')
    },
  })
  extension.activate(fakeContext())
  const { view, posts, receive } = fakeView()
  captures.provider.resolveWebviewView(view)

  await receive({ type: 'context.compact', requestId: 2 })
  await flush()
  assert.equal(discoverCalls, 0, 'context command must not rediscover')

  await receive({ type: 'ready', requestId: 3 })
  await flush()
  assert.equal(discoverCalls, 1, 'explicit Refresh is the only recovery entry point')
  assert.equal(
    posts.some((post) => post.type === 'error'),
    true,
  )
})
