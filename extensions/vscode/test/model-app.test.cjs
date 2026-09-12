// End-to-end MODEL workflow through the REAL built VS Code webview: built bundle
// -> the one production ModelSwitcher (shared with the Dashboard through the
// public @kohakuterrarium/chat-ui seam) -> the real Host model bridge
// (modelBridge -> shims/api.js). The Host is a postMessage fixture that answers
// the fixed ``ready``/socket envelopes AND the four fixed model routes, so this
// pins the WORKFLOW the webview drives — open drawer, real inventory search and
// variation, canonical switch, metadata refresh, and a stale switch result that
// must not write the new selected-creature context — not a component's shape.
const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontendRoot = path.resolve(root, '..', '..', 'src', 'kohakuterrarium-frontend')
const frontendRequire = createRequire(path.join(frontendRoot, 'package.json'))
const { JSDOM, VirtualConsole } = frontendRequire('jsdom')
const importLocal = (name) => import(pathToFileURL(require.resolve(name)))

const RUNTIME = 'graph-live-model'
const ROOT_CREATURE = 'root'
const OTHER_CREATURE = 'beta'

const MODELS = [
  { provider: 'codex', name: 'current', model: 'current', available: true },
  { provider: 'codex', name: 'other', model: 'other', available: true, variation_groups: { effort: { high: {}, low: {} } } },
]
const SWITCHED = { status: 'switched', model: 'codex/other@effort=high' }
const METADATA = {
  session_id: RUNTIME,
  type: 'terrarium',
  creatures: [{ creature_id: 'c1', name: ROOT_CREATURE, llm_name: 'codex/other@effort=high', model: 'other', max_context: 200000 }],
}

let cachedBundle = null
async function buildWebview() {
  if (cachedBundle) return cachedBundle
  const { build } = await importLocal('vite')
  const { default: config } = await import(pathToFileURL(path.join(root, 'vite.config.mjs')))
  const { default: vue } = await importLocal('@vitejs/plugin-vue')
  const { default: autoImport } = await importLocal('unplugin-auto-import/vite')
  const result = await build({
    ...config,
    configFile: false,
    logLevel: 'silent',
    plugins: [
      vue(),
      ...config.plugins.filter((plugin) => plugin?.name !== 'vite:vue' && plugin?.name !== 'unplugin-auto-import'),
      autoImport({ imports: ['vue', 'pinia'], dts: false }),
    ],
    build: { ...config.build, write: false, sourcemap: false, minify: false },
  })
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((item) => item.output)
  cachedBundle = outputs.find((item) => item.type === 'chunk' && item.isEntry).code
  return cachedBundle
}

async function settle(n = 12) {
  for (let i = 0; i < n; i++) await new Promise((resolve) => setImmediate(resolve))
}

async function boot({ holdSwitch = false, holdMetadata = false, metadataError = null, lang = '' } = {}) {
  const code = await buildWebview()
  const errors = []
  const virtualConsole = new VirtualConsole()
  virtualConsole.on('jsdomError', (error) => errors.push(error))
  virtualConsole.on('error', (...args) => errors.push(args))
  const dom = new JSDOM('<!doctype html><div id="app"></div>', {
    url: 'https://webview.test/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole,
  })
  const { window } = dom
  const { document } = window
  // The host language is read once by the shared locale store at first use.
  if (lang) document.documentElement.lang = lang
  const requests = []
  const session = {
    runtimeId: RUNTIME,
    title: 'Model Session',
    kind: 'terrarium',
    isLive: true,
    creatures: [
      { id: 'c1', name: ROOT_CREATURE },
      { id: 'c2', name: OTHER_CREATURE },
    ],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'c1' }
  const held = []
  const heldMetadata = []
  let socketGeneration = 0
  let liveSockets = new Set()
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'ready') {
          socketGeneration++
          for (const socketId of liveSockets) receive({ type: 'ws.closed', socketId, code: 1000 })
          liveSockets = new Set()
          return
        }
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.history') reply(message, { events: [] })
        if (message.type === 'http.historyPage')
          reply(message, {
            events: [],
            messages: [],
            history_page: {
              history_id: 'fixture-history',
              stream: message.options?.stream || 'events',
              before: null,
              after: null,
              has_older: false,
              has_newer: false,
              reset_required: false,
            },
          })
        // The four fixed Host model routes the bridge installs.
        if (message.type === 'http.modelDirectory') reply(message, MODELS)
        if (message.type === 'http.commandInventory') reply(message, { commands: [], skills: [] })
        if (message.type === 'http.instanceMetadata') {
          if (holdMetadata) heldMetadata.push(message)
          else if (metadataError) receive({ type: 'error', requestId: message.requestId, error: metadataError, status: 500 })
          else reply(message, METADATA)
        }
        if (message.type === 'http.switchModel') {
          if (holdSwitch) held.push(message)
          else reply(message, SWITCHED)
        }
        if (message.type === 'ws.open') {
          const generation = socketGeneration
          liveSockets.add(message.socketId)
          queueMicrotask(() => {
            if (generation === socketGeneration) receive({ type: 'ws.opened', socketId: message.socketId })
          })
        }
        if (message.type === 'ws.close') {
          liveSockets.delete(message.socketId)
          receive({ type: 'ws.closed', socketId: message.socketId })
        }
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(16)
  return {
    window,
    document,
    sent,
    errors,
    readyId: ready.requestId,
    receive,
    releaseSwitch: (data = SWITCHED) => held.splice(0).forEach((request) => reply(request, data)),
    releaseMetadata: (data = METADATA) => heldMetadata.splice(0).forEach((request) => reply(request, data)),
    failMetadata: (error = 'metadata unavailable') =>
      heldMetadata.splice(0).forEach((request) => receive({ type: 'error', requestId: request.requestId, error, status: 500 })),
    close: () => window.close(),
  }
}

const text = (node) => (node ? node.textContent : '')
const pill = (document) => document.querySelector('.model-pill')
const clickSwitch = (document) => [...document.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Switch')
const warning = (document) => document.querySelector('.kt-notification-warning')

async function switchToOther(app) {
  pill(app.document).click()
  await settle()
  const row = [...app.document.querySelectorAll('.model-row')].find((node) => node.textContent.includes('other'))
  row.click()
  await settle()
  clickSwitch(app.document).click()
  await settle(16)
}

test('the built webview opens the shared drawer, searches the real inventory, switches with a variation and refreshes metadata', async () => {
  const app = await boot()
  try {
    const modelPill = pill(app.document)
    assert.ok(modelPill, 'the shared ModelSwitcher pill rendered in the header')
    modelPill.click()
    await settle()

    // The real host-keyed directory crossed the Host bridge and rendered rows.
    assert.ok(app.sent('http.modelDirectory').length >= 1, 'the model directory crossed the Host bridge')
    const row = (name) => [...app.document.querySelectorAll('.model-row')].find((node) => node.textContent.includes(name))
    assert.ok(row('other'), 'the shipped inventory row rendered')

    // Real inventory search narrows the list.
    const search = app.document.querySelector('.model-switcher-drawer input')
    assert.ok(search, 'the real Element Plus search input rendered')
    search.value = 'other'
    search.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    await settle()
    assert.equal([...app.document.querySelectorAll('.model-row')].length, 1, 'search filtered the inventory')

    row('other').click()
    await settle()
    const chip = [...app.document.querySelectorAll('.variation-chip')].find((node) => node.textContent.trim() === 'high')
    assert.ok(chip, 'the variation chips rendered from the inventory metadata')
    chip.click()
    await settle()

    clickSwitch(app.document).click()
    await settle(16)

    const switchRequest = app.sent('http.switchModel').at(-1)
    assert.ok(switchRequest, 'the switch crossed the Host bridge')
    assert.equal(switchRequest.session, RUNTIME)
    assert.equal(switchRequest.creature, ROOT_CREATURE)
    assert.equal(switchRequest.model, 'codex/other@effort=high')
    // Canonical pill: the backend's identifier, with the variation summary.
    assert.match(text(pill(app.document)), /codex\/other/)
    assert.match(text(pill(app.document)), /effort=high/)
    // A successful switch triggers a session-metadata refresh.
    assert.ok(app.sent('http.instanceMetadata').length >= 1, 'the metadata refresh crossed the Host bridge')
    assert.equal(app.sent('http.instanceMetadata').at(-1).session, RUNTIME)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('a stale switch result does not write the new selected-creature context after a target change', async () => {
  const app = await boot({ holdSwitch: true })
  try {
    const modelPill = pill(app.document)
    modelPill.click()
    await settle()
    const row = [...app.document.querySelectorAll('.model-row')].find((node) => node.textContent.includes('other'))
    row.click()
    await settle()
    clickSwitch(app.document).click()
    await settle()
    assert.equal(app.sent('http.switchModel').length, 1, 'the switch was posted')

    // The selected creature changes at the SAME ready before the switch result
    // is delivered (a real Host selection.changed notification).
    app.receive({
      type: 'selection.changed',
      readyId: app.readyId,
      connectionId: 'service-a',
      data: { selection: { session: RUNTIME, targetCreatureId: 'c2' }, changed: true, selectionVersion: 2 },
    })
    await settle(12)

    // The late success arrives for the OLD creature: it must not be applied.
    app.releaseSwitch(SWITCHED)
    await settle(16)

    assert.equal(app.sent('http.switchModel').length, 1, 'no retry of the mutation')
    assert.equal(app.sent('http.instanceMetadata').length, 0, 'no metadata refresh for a superseded switch')
    assert.doesNotMatch(text(pill(app.document)), /codex\/other/, 'the stale canonical never reached the new target pill')
  } finally {
    app.close()
  }
})

test('a successful switch keeps the canonical when the metadata read-back fails, warning once without retry', async () => {
  const app = await boot({ metadataError: 'metadata unavailable' })
  try {
    const before = app.sent('http.instanceMetadata').length
    await switchToOther(app)

    assert.equal(app.sent('http.switchModel').length, 1, 'the mutation is never retried')
    assert.equal(app.sent('http.instanceMetadata').length, before + 1, 'the read-back is attempted exactly once')
    // The backend accepted the canonical selector; a failed read-back must not undo it.
    assert.match(text(pill(app.document)), /codex\/other/, 'the accepted canonical is kept')
    const failure = warning(app.document)
    assert.ok(failure, 'the unconfirmed read-back is surfaced as a warning')
    assert.match(failure.textContent, /could not be confirmed/, 'the warning is truthful, not a fabricated rollback')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('a read-back that fails after the target changed is neither applied nor reported', async () => {
  const app = await boot({ holdMetadata: true })
  try {
    await switchToOther(app)
    assert.equal(app.sent('http.instanceMetadata').length, 1, 'the read-back is in flight')

    // The selected creature changes while the read-back is outstanding.
    app.receive({
      type: 'selection.changed',
      readyId: app.readyId,
      connectionId: 'service-a',
      data: { selection: { session: RUNTIME, targetCreatureId: 'c2' }, changed: true, selectionVersion: 2 },
    })
    await settle(12)

    // The late read-back FAILS for the superseded target: no false warning.
    app.failMetadata('stale read')
    await settle(16)

    assert.equal(warning(app.document), null, 'no warning for a read-back the webview no longer owns')
    assert.equal(app.sent('http.instanceMetadata').length, 1, 'no retry of the read-back')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('the unconfirmed read-back warning comes from the shared localized dictionary', async () => {
  const app = await boot({ metadataError: 'boom', lang: 'zh-CN' })
  try {
    await switchToOther(app)
    const failure = warning(app.document)
    assert.ok(failure, 'the unconfirmed read-back is surfaced')
    assert.match(failure.textContent, /无法确认/, 'the warning is localized through the shared dictionary')
    assert.doesNotMatch(failure.textContent, /could not be confirmed/, 'no private English Extension string')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})
