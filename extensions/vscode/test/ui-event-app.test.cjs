// End-to-end UI-event rendering through the REAL built VS Code webview: built
// bundle -> shared chat store -> the one production UIEventBlock (real Element
// Plus widgets, card Markdown, defaults, progress) that the Dashboard also
// renders. The Host is a postMessage fixture (the composer-refresh pattern) that
// answers the fixed ``ready``/socket envelopes, so this pins the WORKFLOW the
// webview drives — including the real ``ui_reply`` chat frame — not a
// component's initial shape.
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

const RUNTIME = 'runtime-ui-event'
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
  const dom = new JSDOM('<!doctype html><div id="app"></div>', {
    url: 'https://webview.test/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole,
  })
  const { window } = dom
  const { document } = window
  if (theme) document.body.className = theme === 'dark' ? 'vscode-dark' : 'vscode-light'
  document.documentElement.setAttribute('lang', lang)
  const requests = []
  const session = {
    runtimeId: RUNTIME,
    title: 'UI Event Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
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
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)
  const activeSocket = () => sent('ws.open').at(-1)?.socketId
  const frame = (data) => receive({ type: 'ws.frame', socketId: activeSocket(), data: JSON.stringify({ source: CREATURE, ...data }) })
  const uiReplies = () =>
    sent('ws.send')
      .map((message) => {
        try {
          return JSON.parse(message.data)
        } catch {
          return null
        }
      })
      .filter((payload) => payload?.type === 'ui_reply')

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(14)
  return { window, document, frame, sent, uiReplies, receive, errors, close: () => window.close() }
}

test('built App renders the shared production UI-event card, its Markdown and safe link actions', async () => {
  const app = await boot()
  try {
    app.frame({
      type: 'card',
      event_id: 'evt-card',
      interactive: true,
      payload: {
        title: 'Deploy',
        subtitle: 'production',
        body: '**ready** to ship',
        fields: [{ label: 'Status', value: 'Ready' }],
        actions: [
          { id: 'bad', label: 'Bad', style: 'link', url: 'javascript:alert(1)' },
          { id: 'good', label: 'Good', style: 'link', url: 'https://example.com' },
        ],
      },
    })
    await settle()
    const card = app.document.querySelector('.ui-event-card')
    assert.ok(card, 'the shared UIEventBlock card rendered')
    assert.match(card.textContent, /Deploy/)
    assert.match(card.textContent, /production/)
    assert.match(card.textContent, /ready/)
    assert.match(card.textContent, /to ship/)
    assert.match(card.textContent, /Status/)
    const links = card.querySelectorAll('.el-link')
    assert.equal(links.length, 1, 'only the safe http(s) link action is rendered')
    assert.equal(links[0].getAttribute('href'), 'https://example.com/')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App submits a button UI-event action as a real chat ui_reply frame', async () => {
  const app = await boot()
  try {
    app.frame({
      type: 'confirm',
      event_id: 'evt-confirm',
      interactive: true,
      payload: { prompt: 'Proceed?', options: [{ id: 'yes', label: 'Proceed' }] },
    })
    await settle()
    const button = [...app.document.querySelectorAll('.ui-event-card button')].find((node) => node.textContent.includes('Proceed'))
    assert.ok(button, 'the confirm action button rendered')
    button.click()
    await settle(16)
    const replies = app.uiReplies()
    assert.ok(replies.length >= 1, 'a ui_reply frame crossed the chat transport')
    assert.equal(replies.at(-1).event_id, 'evt-confirm')
    assert.equal(replies.at(-1).action_id, 'yes')
    assert.equal(replies.at(-1).target, CREATURE)
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('built App submits ask_text with the keyboard and a multi-selection default', async () => {
  const app = await boot()
  try {
    app.frame({
      type: 'ask_text',
      event_id: 'evt-text',
      interactive: true,
      payload: { prompt: 'Name?', default: 'Preset' },
    })
    await settle()
    const input = app.document.querySelector('.ui-event-card input.el-input__inner')
    assert.ok(input, 'the shared ask_text input rendered')
    assert.equal(input.value, 'Preset')
    input.value = 'Keyboard'
    input.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    input.dispatchEvent(new app.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await settle(16)
    assert.equal(app.uiReplies().at(-1)?.action_id, 'submit')
    assert.deepEqual(app.uiReplies().at(-1)?.values, { text: 'Keyboard' })

    app.frame({
      type: 'selection',
      event_id: 'evt-select',
      interactive: true,
      payload: {
        prompt: 'Pick',
        multi: true,
        default: ['a'],
        options: [
          { id: 'a', label: 'A' },
          { id: 'b', label: 'B' },
        ],
      },
    })
    await settle()
    const boxes = app.document.querySelectorAll('.ui-event-card input[type="checkbox"]')
    assert.equal(boxes.length, 2, 'the multi-selection renders real checkboxes')
    assert.equal(boxes[0].checked, true, 'the payload default prefilled the first choice')
    boxes[1].checked = true
    boxes[1].dispatchEvent(new app.window.Event('change', { bubbles: true }))
    const submit = [...app.document.querySelectorAll('.ui-event-card button')].find((node) => node.textContent.includes('Submit'))
    submit.click()
    await settle(16)
    assert.deepEqual(app.uiReplies().at(-1)?.values, { selected: ['a', 'b'] })
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('built App keeps UI-event controls reachable under a narrow viewport, host theme and locale', async () => {
  const app = await boot({ lang: 'zh-cn', theme: 'dark' })
  try {
    app.window.innerWidth = 320
    app.document.documentElement.style.width = '320px'
    assert.equal(app.document.documentElement.classList.contains('dark'), true, 'the host theme is mirrored')
    app.frame({
      type: 'selection',
      event_id: 'evt-narrow',
      interactive: true,
      payload: {
        prompt: 'Pick',
        options: [
          { id: 'a', label: 'A' },
          { id: 'b', label: 'B' },
        ],
      },
    })
    await settle()
    const card = app.document.querySelector('.ui-event-card')
    assert.ok(card, 'the UI-event widget renders at 320px')
    assert.ok(card.querySelector('input[type="radio"]'), 'the choice control is present')
    assert.ok(
      [...card.querySelectorAll('button')].some((node) => node.textContent.includes('Submit')),
      'the submit control is present',
    )
    assert.deepEqual(app.errors, [], 'narrow/theme/locale rendering adds no runtime errors')
  } finally {
    app.close()
  }
})

test('built App routes card and Markdown clicks to the Host opener without navigating', async () => {
  const app = await boot({ lang: 'zh-cn' })
  try {
    app.frame({
      type: 'card',
      event_id: 'evt-link',
      payload: {
        title: 'Docs',
        body: 'See [docs](/docs/page?x=1#top)',
        actions: [{ id: 'open', label: 'Open', style: 'link', url: '/docs/page?x=1#top' }],
      },
    })
    await settle()
    const card = app.document.querySelector('.ui-event-card')
    assert.ok(card, 'the card rendered')
    const actionLink = card.querySelector('.card-link')
    assert.ok(actionLink, 'the host card link rendered')
    actionLink.click()
    await settle()
    const markdownLink = card.querySelector('.md-content a[href="/docs/page?x=1#top"]')
    assert.ok(markdownLink, 'the card Markdown link rendered')
    markdownLink.click()
    await settle(16)

    const opens = app.sent('platform.openLink')
    assert.equal(opens.length, 2, 'both the card action and the Markdown link requested the Host opener')
    assert.equal(opens[0].target, '/docs/page?x=1#top')
    assert.equal(opens[1].target, '/docs/page?x=1#top')
    assert.ok(Number.isSafeInteger(opens[0].readyId), 'the request carries the ready fence')
    assert.equal(
      app.errors.some((error) => String(error?.message || error).includes('navigation')),
      false,
      'a host-owned link never navigates the webview document',
    )
    const notice = app.document.querySelector('.kt-notification')
    assert.ok(notice, 'the localized open failure is surfaced')
    assert.match(notice.textContent, /无法打开链接/)
  } finally {
    app.close()
  }
})
