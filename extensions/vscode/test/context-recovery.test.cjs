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

test('disconnected context and branch actions reject without attempting rediscovery', async () => {
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
  for (const request of [
    { type: 'http.regenerate', requestId: 21, session: 'g', creature: 'root', readyId: 1 },
    { type: 'http.editMessage', requestId: 22, session: 'g', creature: 'root', readyId: 1, msgIdx: -1, content: 'invalid index' },
    { type: 'http.regenerate', requestId: 23, session: 'g', creature: 'root', readyId: 1, url: 'https://forbidden.invalid' },
  ]) {
    await receive(request)
    const failure = posts.find((post) => post.requestId === request.requestId)
    assert.ok(failure, 'a correlatable branch refusal must settle a no-deadline request')
    assert.equal(failure.type, 'error')
    assert.equal(failure.mayHaveRun, false)
    assert.equal(failure.status, undefined, 'pre-admission refusal is not an HTTP response')
    assert.equal(failure.code, 'branch_mutation_failed')
    assert.equal(discoverCalls, 0)
  }
  const beforeAmbiguous = posts.length
  await receive({ type: 'http.editMessage', requestId: 24, socketId: 24 })
  await receive({ type: 'http.regenerate', requestId: '25' })
  assert.equal(posts.length, beforeAmbiguous, 'ambiguous or invalid request IDs remain ignored')
  assert.equal(
    posts.some((post) => post.type === 'context.compact.result'),
    false,
  )
})

test('clipboard writes use the host without a runtime and acknowledge only the completed write', async (t) => {
  const { allowedMessage } = require('../src/host/protocol.cjs')
  const { vscode, captures } = fakeVscode()
  const writes = []
  const logs = []
  t.mock.method(console, 'error', (...args) => logs.push(args.map(String).join(' ')))
  let discoverCalls = 0
  let finishWrite
  let clipboard = 'previous'
  const text = 'copied-only-secret\\n中文 🌱 <not html>'
  vscode.env.clipboard = {
    writeText(value) {
      writes.push(value)
      return new Promise((resolve) => {
        finishWrite = () => {
          clipboard = value
          resolve()
        }
      })
    },
  }
  const extension = loadExtension(vscode, {
    discoverInstalledKt: async () => {
      discoverCalls++
      throw Error('clipboard must not discover a runtime')
    },
  })
  extension.activate(fakeContext())
  const { view, posts, receive } = fakeView()
  captures.provider.resolveWebviewView(view)
  const request = { type: 'platform.writeClipboard', requestId: 71, text }
  assert.equal(allowedMessage(request), true)
  const pending = receive(request)
  try {
    await flush()
    assert.deepEqual(writes, [text])
    assert.equal(clipboard, 'previous')
    assert.deepEqual(posts, [])
  } finally {
    finishWrite?.()
    await pending
  }
  assert.equal(clipboard, text)
  assert.deepEqual(posts, [{ type: 'platform.writeClipboard.result', requestId: 71, data: { written: true } }])

  for (const data of [{ text: 5 }, { text, format: 'html' }, { text, command: 'workbench.any' }, { text, endpoint: 'http://evil' }]) {
    const invalid = { type: 'platform.writeClipboard', requestId: 72, ...data }
    assert.equal(allowedMessage(invalid), false)
    await receive(invalid)
  }
  await receive({ type: 'platform.readClipboard', requestId: 72 })
  assert.deepEqual(writes, [text])
  assert.equal(posts.length, 1)

  vscode.env.clipboard.writeText = async (value) => {
    writes.push(value)
    clipboard = value
  }
  await receive({ type: 'platform.writeClipboard', requestId: 73, text: '' })
  assert.equal(clipboard, '')
  assert.deepEqual(posts.at(-1), { type: 'platform.writeClipboard.result', requestId: 73, data: { written: true } })

  vscode.env.clipboard.writeText = async () => {
    throw Object.assign(Error(text), { status: 403 })
  }
  await receive({ type: 'platform.writeClipboard', requestId: 74, text })
  assert.equal(posts.at(-1).type, 'error')
  assert.equal(posts.at(-1).code, 'clipboard_write_failed')
  assert.equal(posts.at(-1).status, undefined)
  assert.equal(posts.at(-1).mayHaveRun, undefined)
  assert.equal(JSON.stringify(posts).includes('copied-only-secret'), false)
  assert.equal(logs.join(' ').includes('copied-only-secret'), false)
  delete vscode.env.clipboard
  await receive({ type: 'platform.writeClipboard', requestId: 75, text })
  assert.equal(posts.at(-1).code, 'clipboard_write_failed')
  assert.equal(discoverCalls, 0)
})

test('explicit ready remains the only rediscovery entry point', async () => {
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
