// End-to-end inline UI and widgets through the REAL built VS Code webview: built
// bundle -> shared chat store -> the shared AssistantText / kt.ui tree. The Host
// is a postMessage fixture that answers the fixed ``ready``/socket envelopes, so
// this pins the WORKFLOW: the renderer announcement, an inline ``<ui>`` block
// that keeps state and sends a user message, ``kt.app`` apps opened in editor
// tabs (a webview cannot run app frames) with their bridge relayed through the
// Host, and Mermaid loaded lazily from ``dist/mermaid.js``.
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

const RUNTIME = 'runtime-inline-ui'
const CREATURE = 'alpha'

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

async function boot({ lang = 'en', theme = '' } = {}) {
  const code = await buildWebview()
  const errors = []
  const virtualConsole = new VirtualConsole()
  virtualConsole.on('jsdomError', (error) => errors.push(error))
  virtualConsole.on('error', (...args) => errors.push(args))
  const dom = new JSDOM(
    '<!doctype html><div id="app" data-mermaid-uri="https://webview.test/dist/mermaid.js" data-style-nonce="styleNONCE1"></div>',
    {
      url: 'https://webview.test/',
      runScripts: 'outside-only',
      pretendToBeVisual: true,
      virtualConsole,
    },
  )
  const { window } = dom
  const { document } = window
  if (theme) document.body.className = theme === 'dark' ? 'vscode-dark' : 'vscode-light'
  document.documentElement.setAttribute('lang', lang)
  const requests = []
  const session = {
    runtimeId: RUNTIME,
    title: 'Inline UI Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  // The session history the fixture serves; a test fills it once the turn is recorded.
  const history = { events: [], messages: [] }
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
        if (message.type === 'http.history') reply(message, { events: history.events, messages: history.messages })
        if (message.type === 'http.historyPage')
          reply(message, {
            events: history.events,
            messages: history.messages,
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
        if (message.type === 'ws.send' && liveSockets.has(message.socketId))
          receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
        // The Host owns link resolution/opening; this fixture declines so the
        // webview surfaces its localized failure through the notification region.
        if (message.type === 'platform.openLink') receive({ type: 'error', requestId: message.requestId, error: 'Could not open link' })
        // The Host's editor-tab apps: an open and a relayed post both succeed.
        if (message.type === 'app.open') reply(message, { opened: true })
        if (message.type === 'app.post') reply(message, { posted: true })
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)
  const activeSocket = () => sent('ws.open').at(-1)?.socketId
  const frame = (data) => receive({ type: 'ws.frame', socketId: activeSocket(), data: JSON.stringify({ source: CREATURE, ...data }) })
  const frames = (type) =>
    sent('ws.send')
      .map((message) => JSON.parse(message.data))
      .filter((payload) => payload?.type === type)

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(14)
  return { window, document, frame, frames, history, sent, receive, errors, close: () => window.close() }
}

const BLOCK =
  '<ui title="Tip" layout="page"><state tip={10}/><stat label="Tip">{tip}%</stat><number label="Tip" bind="tip"/><button send="Use a {tip}% tip">Use it</button></ui>'
const HTML_BLOCK = '<ui><html><b>custom</b></html></ui>'
const REPLY = `Pick a tip:\n\n${BLOCK}\n\nA picture:\n\n${HTML_BLOCK}\n\n\`\`\`mermaid\ngraph LR; A --> B\n\`\`\`\n`

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('built App announces kt.ui and kt.app, draws inline UI, runs apps in editor tabs, keeps block state and loads Mermaid lazily', async () => {
  const app = await boot()
  try {
    // The view draws kt.ui and runs apps in editor tabs, so it announces both.
    const hello = app.frames('client_hello').at(-1)
    assert.ok(hello, 'the store announced its renderers')
    assert.equal(hello.renderers['kt.ui'], 1)
    assert.equal(hello.renderers['kt.app'], 1)

    // Mermaid draws on demand: stub the browser build the shim loads.
    app.window.mermaid = {
      initialize() {},
      render: async (id) => ({ svg: `<svg data-test="mermaid-svg"><style>#${id}{fill:red}</style></svg>` }),
    }

    // One recorded turn: the post-turn history resync gives the reply its (turn, branch) address.
    const turn = { turn_index: 1, branch_id: 1 }
    app.history.events.push(
      { type: 'user_input', content: 'tip?', event_id: 1, ...turn },
      { type: 'processing_start', event_id: 2, ...turn },
      { type: 'text', content: REPLY, event_id: 3, ...turn },
      { type: 'processing_end', event_id: 4, ...turn },
    )
    app.history.messages.push({ role: 'user', content: 'tip?' }, { role: 'assistant', content: REPLY })
    app.frame({ type: 'processing_start', ...turn })
    // A long tool call streams its arguments: the processing line says so.
    app.frame({ type: 'tool_args_progress', name: 'write', chars: 12800, ...turn })
    await settle(20)
    const processing = app.document.querySelector('.kt-transcript-processing__label')
    assert.ok(processing, 'the processing line is shown')
    assert.equal(processing.textContent, 'Writing write arguments… 12.5 KB')
    app.frame({ type: 'text', content: REPLY })
    app.frame({ type: 'processing_end', ...turn })
    app.frame({ type: 'idle' })
    await settle(20)
    await wait(400)
    await settle(20)
    assert.equal(app.document.querySelector('[data-test="inline-ui-open-canvas"]'), null, 'the view has no canvas to open')

    const stat = app.document.querySelector('[data-test="ktui-stat-value"]')
    assert.ok(stat, 'the inline block rendered as kt.ui components')
    assert.equal(stat.textContent.trim(), '10%')
    assert.ok(!app.document.body.textContent.includes('<ui'), 'no raw markup reaches the transcript')

    // Inline html opens in an editor tab: the webview creates no frame and sends the sandboxed document.
    const launcher = app.document.querySelector('[data-test="app-launcher"]')
    assert.ok(launcher, 'inline html shows the app launcher')
    assert.equal(app.document.querySelector('iframe'), null, 'no app frame is created in the webview')
    launcher.querySelector('[data-test="app-launcher-open"]').click()
    await settle(20)
    const inlineOpen = app.sent('app.open').at(-1)
    assert.ok(inlineOpen, 'the launcher asked the Host for an editor tab')
    assert.match(inlineOpen.appId, /^h:[0-9a-f]{8}$/)
    assert.ok(inlineOpen.html.startsWith('<meta http-equiv="Content-Security-Policy"'))
    assert.match(inlineOpen.html, /connect-src 'none'/)
    assert.match(inlineOpen.html, /window\.__ktTransport/)
    assert.ok(inlineOpen.html.endsWith('<b>custom</b>'))

    // Mermaid's browser build is requested once, from the Extension's dist, and the SVG style carries the style nonce.
    const scripts = [...app.document.querySelectorAll('script[src]')]
    assert.deepEqual(
      scripts.map((node) => node.getAttribute('src')),
      ['https://webview.test/dist/mermaid.js'],
    )
    scripts[0].dispatchEvent(new app.window.Event('load'))
    await settle(20)
    const diagram = app.document.querySelector('.kt-mermaid[data-state="done"] [data-test="mermaid-svg"]')
    assert.ok(diagram, 'the mermaid fence became a diagram')
    assert.equal(diagram.querySelector('style').getAttribute('nonce'), 'styleNONCE1')

    // The button sends its text, built from block state, as a normal user message.
    const button = app.document.querySelector('[data-test="ktui-button"]')
    assert.ok(button, 'the send button rendered')
    button.click()
    await settle(20)
    const inputs = app.frames('input')
    assert.equal(inputs.length, 1, 'one click, one chat send')
    assert.match(JSON.stringify(inputs[0]), /"Use a 10% tip"/)

    // Another client moved the block's state: the frame for this block's address updates this view.
    const { blockHash } = await import(pathToFileURL(path.join(frontendRoot, 'src/components/chat/widgets/ktui/blockId.js')))
    app.frame({ type: 'inline_ui_state', ui_turn: 1, ui_branch: 1, block: `${blockHash(BLOCK)}.0`, state: { tip: 18 } })
    await settle(20)
    assert.equal(app.document.querySelector('[data-test="ktui-stat-value"]').textContent.trim(), '18%')
    app.frame({ type: 'inline_ui_state', ui_turn: 1, ui_branch: 1, block: 'deadbeef.0', state: { tip: 99 } })
    await settle(20)
    assert.equal(
      app.document.querySelector('[data-test="ktui-stat-value"]').textContent.trim(),
      '18%',
      'another block address changes nothing',
    )

    // An edit here is saved for the block's address over the chat socket (debounced).
    const increase = app.document.querySelector('.el-input-number__increase')
    assert.ok(increase, 'the bound number input rendered')
    increase.dispatchEvent(new app.window.MouseEvent('mousedown', { bubbles: true, button: 0 }))
    app.document.dispatchEvent(new app.window.MouseEvent('mouseup', { bubbles: true, button: 0 }))
    await settle(20)
    assert.equal(app.document.querySelector('[data-test="ktui-stat-value"]').textContent.trim(), '19%')
    await wait(700)
    const saved = app.frames('inline_ui_state')
    assert.equal(saved.length, 1, 'one debounced state frame')
    assert.deepEqual(
      { turn: saved[0].ui_turn, branch: saved[0].ui_branch, block: saved[0].block, state: saved[0].state },
      { turn: 1, branch: 1, block: `${blockHash(BLOCK)}.0`, state: { tip: 19 } },
    )

    // A kt.app widget opens in a tab too; its bridge is relayed through the Host.
    app.frame({
      type: 'widget',
      event_id: 'w_app',
      payload: { renderer: 'kt.app', title: 'Explorer', spec: { html: '<p>explorer</p>' } },
    })
    await settle(20)
    const launchers = app.document.querySelectorAll('[data-test="app-launcher"]')
    assert.equal(launchers.length, 2)
    assert.match(launchers[1].textContent, /Explorer/)
    launchers[1].querySelector('[data-test="app-launcher-open"]').click()
    await settle(20)
    const widgetOpen = app.sent('app.open').at(-1)
    assert.deepEqual([widgetOpen.appId, widgetOpen.title], ['w:w_app', 'Explorer'])
    assert.equal(launchers[1].querySelector('[data-test="app-launcher-open"]').textContent.trim(), 'Show app')

    const fromTab = (message, interacted) =>
      app.receive({ type: 'app.message', appId: 'w:w_app', message, ...(interacted ? { interacted } : {}) })
    fromTab({ jsonrpc: '2.0', id: 1, method: 'ui/initialize', params: {} })
    await settle(20)
    const initReply = app.sent('app.post').at(-1)
    assert.equal(initReply.appId, 'w:w_app')
    assert.deepEqual([initReply.message.id, initReply.message.result.widgetId], [1, 'w_app'])

    // Without the Host's confirmation a send is refused; with it the widget message reaches the agent.
    fromTab({ jsonrpc: '2.0', id: 2, method: 'kt/send', params: { name: 'pick', data: { v: 1 } } })
    await settle(20)
    assert.equal(app.frames('widget_msg').length, 0)
    assert.match(app.sent('app.post').at(-1).message.error.message, /recent click/)
    fromTab({ jsonrpc: '2.0', id: 3, method: 'kt/send', params: { name: 'pick', data: { v: 1 } } }, true)
    await settle(20)
    const sentMsg = app.frames('widget_msg')
    assert.equal(sentMsg.length, 1)
    assert.deepEqual([sentMsg[0].event_id, sentMsg[0].name, sentMsg[0].data, sentMsg[0].origin], ['w_app', 'pick', { v: 1 }, 'app'])

    app.receive({ type: 'app.closed', appId: 'w:w_app' })
    await settle(20)
    assert.equal(launchers[1].querySelector('[data-test="app-launcher-open"]').textContent.trim(), 'Open app')
    assert.equal(app.document.querySelector('iframe'), null)

    await wait(10)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})
