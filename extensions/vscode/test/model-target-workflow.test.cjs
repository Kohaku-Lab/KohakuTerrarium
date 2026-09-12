// Target-selection workflow through the REAL built VS Code webview: the Host owns
// the live selection and answers the flat ``session.select`` route faithfully.
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

const RUNTIME = 'graph-live-target'
const ROOT_CREATURE = { id: 'c1', name: 'root' }
const BETA_CREATURE = { id: 'c2', name: 'beta' }

const MODELS = [
  { provider: 'codex', name: 'current', model: 'current', available: true },
  { provider: 'codex', name: 'other', model: 'other', available: true },
]
const SWITCHED = { status: 'switched', model: 'codex/other' }

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

const HISTORY = {
  root: [{ role: 'assistant', content: 'alpha transcript', message_id: 'alpha-1' }],
  beta: [{ role: 'assistant', content: 'beta transcript', message_id: 'beta-1' }],
}
const SESSION_INFO = {
  root: {
    type: 'activity',
    activity_type: 'session_info',
    source: 'root',
    session_id: RUNTIME,
    model: 'alpha-model',
    llm_name: 'codex/alpha-model',
    max_context: 111111,
  },
  beta: {
    type: 'activity',
    activity_type: 'session_info',
    source: 'beta',
    session_id: RUNTIME,
    model: 'beta-model',
    llm_name: 'codex/beta-model',
    max_context: 222222,
  },
}

async function boot({ holdSelect = false } = {}) {
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
  const session = {
    runtimeId: RUNTIME,
    title: 'Target Session',
    kind: 'terrarium',
    isLive: true,
    creatures: [ROOT_CREATURE, BETA_CREATURE],
  }
  const requests = []
  let heldSelect = null
  let selection = { session: RUNTIME, targetCreatureId: ROOT_CREATURE.id }
  let selectionVersion = 1
  const creatureOf = (id) => (id === BETA_CREATURE.id ? BETA_CREATURE : ROOT_CREATURE)
  let liveSockets = new Set()
  const socketTarget = new Map()
  let openedBetaSocket = null
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  const closeLive = (code) => {
    for (const socketId of liveSockets) receive({ type: 'ws.closed', socketId, code })
    liveSockets = new Set()
  }
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'ready') {
          closeLive(1000)
          return
        }
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.modelDirectory') reply(message, MODELS)
        if (message.type === 'http.commandInventory') reply(message, { commands: [], skills: [] })
        if (message.type === 'http.instanceMetadata') reply(message, { ...session, targetCreatureId: selection.targetCreatureId })
        if (message.type === 'http.switchModel') reply(message, SWITCHED)
        // The faithful selection owner: apply the new target and rotate sockets
        // (RuntimeHost.rotateGeneration closes the previous generation) FIRST, then
        // hold only the first flat reply so the test can deliver it late.
        if (message.type === 'session.select') {
          selection = { session: message.session, targetCreatureId: message.creatureId }
          selectionVersion += 1
          closeLive(1000)
          const data = {
            session: message.session,
            creature: creatureOf(message.creatureId).name,
            targetCreatureId: message.creatureId,
            readyId: message.readyId,
            selectionVersion,
          }
          if (holdSelect && heldSelect === null) {
            heldSelect = { requestId: message.requestId, request: message, data }
            return
          }
          reply(message, data)
          return
        }
        if (message.type === 'http.history') reply(message, { events: [], messages: HISTORY[creatureOf(selection.targetCreatureId).name] })
        if (message.type === 'http.historyPage') {
          const name = creatureOf(selection.targetCreatureId).name
          const stream = message.options?.stream || 'events'
          reply(message, {
            events: [],
            messages: HISTORY[name],
            history_page: {
              history_id: `history-${name}`,
              stream,
              before: null,
              after: null,
              has_older: false,
              has_newer: false,
              reset_required: false,
            },
          })
        }
        if (message.type === 'ws.open') {
          liveSockets.add(message.socketId)
          socketTarget.set(message.socketId, creatureOf(selection.targetCreatureId).name)
          if (socketTarget.get(message.socketId) === 'beta') openedBetaSocket = message.socketId
          const socketId = message.socketId
          queueMicrotask(() => {
            if (!liveSockets.has(socketId)) return
            receive({ type: 'ws.opened', socketId })
            const name = socketTarget.get(socketId)
            if (name) receive({ type: 'ws.frame', socketId, data: JSON.stringify(SESSION_INFO[name]) })
          })
        }
        if (message.type === 'ws.close') {
          liveSockets.delete(message.socketId)
          receive({ type: 'ws.closed', socketId: message.socketId })
        }
        if (message.type === 'ws.send') receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, {
    available: true,
    automatic: true,
    readyId: ready.requestId,
    connectionId: 'service-a',
    selectionVersion: 1,
    selection,
  })
  await settle(24)
  // Answer the newest pending ``ready`` with the backend's CURRENT selection —
  // the flat result a real Refresh returns. The fixture stays observational: it
  // never invents a superseded Host response.
  const replyReady = () => {
    const request = requests.filter((message) => message.type === 'ready').at(-1)
    reply(request, {
      available: true,
      automatic: true,
      readyId: request.requestId,
      connectionId: 'service-a',
      selectionVersion,
      selection,
    })
  }
  return {
    window,
    document: window.document,
    sent,
    errors,
    readyId: ready.requestId,
    receive,
    replyReady,
    releaseSelect: (requestId) => {
      if (!heldSelect || (requestId != null && heldSelect.requestId !== requestId)) return
      reply(heldSelect.request, heldSelect.data)
      heldSelect = null
    },
    getOpenedBetaSocket: () => openedBetaSocket,
    socketTargets: () => new Map(socketTarget),
    close: () => window.close(),
  }
}

async function pickTarget(app, name) {
  const select = app.document.querySelector('.target-select')
  assert.ok(select, 'the shared target dropdown rendered')
  const wrapper = select.querySelector('.el-select__wrapper') || select.querySelector('input') || select
  wrapper.dispatchEvent(new app.window.MouseEvent('click', { bubbles: true }))
  await settle(8)
  const option = [...app.document.querySelectorAll('.el-select-dropdown__item')].find((node) => node.textContent.trim() === name)
  assert.ok(option, `the ${name} target option rendered`)
  option.dispatchEvent(new app.window.MouseEvent('click', { bubbles: true }))
  await settle(24)
}

const transcriptText = (document) => (document.querySelector('.kt-chat-transcript, .chat-region') || document.body).textContent

test('the ModelSwitcher target dropdown rewires history, socket, composer and model POST to the new creature', async () => {
  const app = await boot()
  try {
    // Baseline: alpha (root) history is displayed before any switch.
    assert.match(transcriptText(app.document), /alpha transcript/)

    await pickTarget(app, 'beta')

    // The dropdown routed through the real selection owner, not a local tab change.
    const selectRequest = app.sent('session.select').at(-1)
    assert.ok(selectRequest, 'the target change crossed the Host selection owner')
    assert.equal(selectRequest.session, RUNTIME)
    assert.equal(selectRequest.creatureId, BETA_CREATURE.id)

    // The fresh creature's history was fetched (after the selection landed) and displayed.
    const historyRequests = app.sent('http.historyPage').filter((message) => message.requestId > selectRequest.requestId)
    assert.ok(historyRequests.length >= 1, 'history was refetched after the target change')
    assert.match(transcriptText(app.document), /beta transcript/, 'the new target history is displayed')
    assert.doesNotMatch(transcriptText(app.document), /alpha transcript/, 'the old target history is gone from the transcript')

    // A FRESH socket was opened for the new selection and seeded a real session_info.
    const betaSocket = app.getOpenedBetaSocket()
    assert.ok(betaSocket != null, 'a fresh socket opened for the new target')
    assert.ok(
      app.sent('ws.open').some((message) => message.socketId === betaSocket),
      'the fresh socket crossed the Host bridge',
    )
    const modelPill = app.document.querySelector('.model-pill')
    assert.ok(modelPill, 'the model pill rendered for the new target')
    assert.match(modelPill.textContent, /beta-model/, 'the fresh session_info seeded the new target model')
    assert.deepEqual(app.errors, [])

    // The real composer sends to the NEW creature over the new socket.
    const textarea = app.document.querySelector('.kt-chat-composer textarea')
    assert.ok(textarea, 'the real composer rendered')
    textarea.value = 'hello beta'
    textarea.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    await settle()
    const send = app.document.querySelector('.kt-chat-composer__primary')
    send.dispatchEvent(new app.window.MouseEvent('click', { bubbles: true }))
    await settle(24)
    const frame = app
      .sent('ws.send')
      .map((message) => JSON.parse(message.data))
      .find((data) => data.type === 'input')
    assert.ok(frame, 'the composer frame crossed the Host bridge')
    assert.equal(app.sent('ws.send').at(-1).socketId, betaSocket, 'the composer sent over the new target socket')
    assert.deepEqual(app.errors, [])

    // A model switch through the shared drawer posts to the NEW creature.
    app.document.querySelector('.model-pill').click()
    await settle()
    const row = [...app.document.querySelectorAll('.model-row')].find((node) => node.textContent.includes('other'))
    assert.ok(row, 'the model row rendered for the new target')
    row.click()
    await settle()
    ;[...app.document.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Switch').click()
    await settle(16)
    const switchRequest = app.sent('http.switchModel').at(-1)
    assert.ok(switchRequest, 'the model switch crossed the Host bridge')
    assert.equal(switchRequest.creature, BETA_CREATURE.name, 'the model mutation targets the new creature')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('a late select reply after the target moved on never rebinds the stale socket or clobbers fresh history', async () => {
  const app = await boot({ holdSelect: true })
  try {
    // Baseline: alpha (root) is the live target.
    assert.match(transcriptText(app.document), /alpha transcript/)

    // 1. The user picks beta. The Host applies the selection and rotates the
    //    socket generation at once, but its flat reply is held in flight.
    await pickTarget(app, 'beta')
    const betaSelect = app.sent('session.select').at(-1)
    assert.equal(app.sent('session.select').length, 1, 'the beta selection is in flight')
    assert.equal(betaSelect.creatureId, BETA_CREATURE.id)

    // 2. A real refresh advances ready; the result reports the backend's current
    //    selection (beta), so the live target finally follows to beta.
    const refresh = app.document.querySelector('button[aria-label="Refresh Sessions"]')
    assert.ok(refresh, 'the Refresh Sessions control rendered')
    refresh.click()
    await settle(8)
    app.replyReady()
    await settle(24)
    assert.equal(app.sent('session.select').length, 1, 'the refresh issued no selection')
    assert.match(transcriptText(app.document), /beta transcript/, 'the live target followed the backend selection')

    // 3. The dropdown now shows beta, so picking root sends a REAL root change.
    await pickTarget(app, 'root')
    const rootSelect = app.sent('session.select').at(-1)
    assert.equal(app.sent('session.select').length, 2, 'the root selection crossed the Host owner')
    assert.equal(rootSelect.creatureId, ROOT_CREATURE.id)
    assert.match(transcriptText(app.document), /alpha transcript/, 'the root target restored alpha history')
    assert.doesNotMatch(transcriptText(app.document), /beta transcript/)
    const rootSocket = app.sent('ws.open').at(-1)
    assert.equal(app.socketTargets().get(rootSocket.socketId), 'root', 'a fresh root socket opened')
    assert.match(app.document.querySelector('.model-pill').textContent, /alpha-model/, 'alpha session_info seeded the model')

    // 4. Deliver the OLD held beta reply. Its flat route was superseded by the
    //    refresh, so it must reopen nothing and leave the newest target alone.
    const socketsBefore = app.sent('ws.open').length
    const historyBefore = app.sent('http.historyPage').length
    app.releaseSelect(betaSelect.requestId)
    await settle(24)
    assert.equal(app.sent('ws.open').length, socketsBefore, 'the stale reply opened no socket')
    assert.equal(app.sent('http.historyPage').length, historyBefore, 'the stale reply refetched no history')
    assert.match(transcriptText(app.document), /alpha transcript/, 'alpha history stayed the newest')
    assert.doesNotMatch(transcriptText(app.document), /beta transcript/)
    assert.match(app.document.querySelector('.model-pill').textContent, /alpha-model/, 'the latest model pill stayed')
    assert.ok(app.document.querySelector('.kt-chat-composer textarea'), 'the composer stayed bound to the latest target')
    assert.equal(app.document.querySelector('.status.is-error'), null, 'no false failure toast')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})
