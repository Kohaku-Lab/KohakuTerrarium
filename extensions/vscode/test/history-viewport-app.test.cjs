// End-to-end paged viewport through the REAL built VS Code webview:
// built bundle -> shim -> installHistoryBridge -> App -> ChatTranscriptSection.
// Drives an actual paged head, an upward older materialize, a source reset,
// and an opaque record-detail read, then pins the unchanged draft/live-append
// behavior. The fixture asserts request cursors and rendered content, not just
// the shape of the controls.
const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.resolve(root, '../../src/kohakuterrarium-frontend/package.json'))
const { JSDOM, VirtualConsole } = frontendRequire('jsdom')
const importLocal = (name) => import(pathToFileURL(require.resolve(name)))

const RUNTIME = 'graph-live'
const TAB = 'alpha'

async function buildWebview() {
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
  return outputs.find((item) => item.type === 'chunk' && item.isEntry).code
}

async function settle(n = 8) {
  for (let i = 0; i < n; i++) await new Promise((resolve) => setImmediate(resolve))
}

// A raw events page mirroring the backend paged contract.
function page(start, count, older, overrides = {}) {
  const events = Array.from({ length: count }, (_, i) => ({
    type: 'user_message',
    event_id: start + i,
    content: `message ${start + i}`,
    _history_key: `e:${start + i}`,
  }))
  return {
    events,
    messages: [],
    live_job_ids: [],
    is_processing: false,
    history_page: {
      version: 1,
      stream: 'events',
      history_id: 'history',
      before: `b${start}`,
      after: `a${start + count - 1}`,
      has_older: older,
      has_newer: false,
      reset_required: false,
    },
    ...overrides,
  }
}

test('built App pages older history, resets, reads record detail, and keeps live/draft behavior', async () => {
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
  const requests = []
  const session = {
    runtimeId: RUNTIME,
    title: 'Viewport Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: TAB }],
  }
  let readyId
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  const pageResponses = []
  const detailResponses = []
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.history') reply(message, { events: [] })
        if (message.type === 'http.historyPage') reply(message, pageResponses.shift() || page(0, 0, false))
        if (message.type === 'http.historyDetail') reply(message, detailResponses.shift() || { history_page: {}, record: null })
        if (message.type === 'ws.open') receive({ type: 'ws.opened', socketId: message.socketId })
        if (message.type === 'ws.close') receive({ type: 'ws.closed', socketId: message.socketId })
        if (message.type === 'ws.send' && JSON.parse(message.data).type === 'input')
          receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
      })
    },
  })
  const rows = () => [...document.querySelectorAll('.kt-transcript-viewport [data-message-id]')]
  const text = () => document.querySelector('.kt-transcript-section')?.textContent || ''
  const pages = () => requests.filter((message) => message.type === 'http.historyPage')
  const details = () => requests.filter((message) => message.type === 'http.historyDetail')

  try {
    window.eval(code)
    await settle()
    const initial = requests.find((message) => message.type === 'ready')
    readyId = initial.requestId
    pageResponses.push(page(400, 200, true))
    reply(initial, { available: true, automatic: true, readyId, connectionId: 'service-a', selectionVersion: 1, selection })
    await settle(12)
    assert.ok(document.querySelector('.composer-region textarea'), 'selected conversation renders the real Composer')
    assert.equal(pages()[0].options.limit, 400)
    assert.equal(rows().length, 200, 'the bounded newest page renders')

    // ── upward older materialize ────────────────────────────────────────
    const beforeFirstRow = rows().map((row) => row.getAttribute('data-message-id'))
    pageResponses.push(page(200, 200, true))
    document.querySelector('.kt-transcript-earlier').click()
    await settle(12)
    const olderRequest = pages()[1]
    assert.equal(olderRequest.options.before, 'b400', 'the older fetch uses the head before-cursor')
    assert.equal(olderRequest.options.history_id, 'history')
    assert.equal(olderRequest.options.stream, 'events')
    assert.equal(rows().length, 400, 'older rows materialize above the head')
    const afterFirstRow = rows().map((row) => row.getAttribute('data-message-id'))
    assert.deepEqual(afterFirstRow.slice(200), beforeFirstRow, 'existing rows keep their physical identity after the merge')
    assert.match(text(), /message 200/)

    // ── reset required ──────────────────────────────────────────────────
    pageResponses.push(page(0, 0, true, { history_page: { ...page(0, 0, true).history_page, reset_required: true } }))
    document.querySelector('.kt-transcript-earlier').click()
    await settle(12)
    const resetControl = document.querySelector('[data-history-reset]')
    assert.ok(resetControl, 'a source reset surfaces an explicit recovery control')
    pageResponses.push(page(1000, 3, false))
    resetControl.click()
    await settle(12)
    const reloadRequest = pages().at(-1)
    assert.equal(reloadRequest.options.limit, 400)
    assert.equal(reloadRequest.options.before, undefined, 'recovery re-reads a fresh bounded head')
    assert.equal(rows().length, 3, 'the recovered page replaces the stale range')
    assert.match(text(), /message 1000/)
    assert.equal(document.querySelector('[data-history-reset]'), null)

    // ── opaque record detail ────────────────────────────────────────────
    const truncated = page(0, 1, false, {
      events: [
        {
          type: 'user_message',
          event_id: 1,
          content: 'preview body',
          _history_key: 'c:1',
          _history_truncated: true,
          _history_detail: 'opaque-token',
        },
      ],
    })
    pageResponses.push(truncated)
    window.dispatchEvent(new window.MessageEvent('message', { data: { type: 'configuration.changed' } }))
    await settle()
    const reconfigure = requests.filter((message) => message.type === 'ready').at(-1)
    readyId = reconfigure.requestId
    pageResponses.push(truncated)
    reply(reconfigure, { available: true, automatic: true, readyId, connectionId: 'service-b', selectionVersion: 2, selection })
    await settle(12)
    const detailControl = document.querySelector('[data-history-detail="c:1"]')
    assert.ok(detailControl, 'a truncated record exposes its detail control')
    detailResponses.push({
      history_page: { history_id: 'history', stream: 'events' },
      record: { ...truncated.events[0], content: 'complete body', _history_truncated: false },
    })
    detailControl.click()
    await settle(12)
    const detailRequest = details().at(-1)
    assert.equal(detailRequest.params.stream, 'events')
    assert.equal(detailRequest.params.history_id, 'history')
    assert.equal(detailRequest.params.ref, 'opaque-token')
    assert.match(text(), /complete body/)
    assert.equal(document.querySelector('[data-history-detail="c:1"]'), null, 'the resolved detail control is withdrawn')

    // ── unchanged live append + draft isolation ─────────────────────────
    const socketId = requests.filter((message) => message.type === 'ws.open').at(-1).socketId
    receive({ type: 'ws.frame', socketId, data: JSON.stringify({ source: TAB, type: 'text', content: 'live tail text' }) })
    await settle(12)
    assert.match(text(), /live tail text/, 'a live stream frame still appends to the transcript')
    const textarea = document.querySelector('.composer-region textarea')
    textarea.value = 'unsent draft'
    textarea.dispatchEvent(new window.Event('input', { bubbles: true }))
    await settle()
    document.querySelector('.kt-transcript-viewport').scrollTop = 0
    document.querySelector('.kt-transcript-viewport').dispatchEvent(new window.Event('scroll'))
    await settle(8)
    assert.equal(document.querySelector('.composer-region textarea').value, 'unsent draft', 'scrolling never disturbs the draft')
    assert.deepEqual(errors, [], 'built webview emitted no browser/runtime errors')
  } finally {
    window.close()
  }
})
