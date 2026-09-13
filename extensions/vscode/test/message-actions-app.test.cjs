// End-to-end MESSAGE-ACTION workflow through the REAL built VS Code webview:
// built bundle -> shared chat store -> the one production MessageRow (copy /
// edit / regenerate controls). The Host is a postMessage fixture (the
// composer-refresh pattern) that answers the fixed ``ready``/socket envelopes,
// the paged history routes with faithful persisted user/assistant events
// (event_id/turn/branch), the fixed branch-mutation routes, and the
// ``platform.writeClipboard`` round-trip (fixed response shape) so the test can
// read the EXACT copied text back. It pins the WORKFLOW the webview drives —
// render persisted history, copy the visible text only, edit+resync, and
// regenerate-once — not a component's initial shape.
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

const RUNTIME = 'graph-live-actions'
const CREATURE = 'alpha'

const USER_TEXT = 'Hello there'
const ASSISTANT_TEXT = '**Sure thing.**'
const TOOL_SECRET = 'SECRET_TOOL_BODY'
const { allowedMessage } = require('../src/host/protocol.cjs')

// Faithful persisted event log: one user turn, one assistant reply carrying a
// tool part (whose body must never leak into a copy), with real ids/turn/branch.
function persistedEvents({ userContent = USER_TEXT, assistantContent = ASSISTANT_TEXT, branch = 1 } = {}) {
  return [
    { event_id: 1, type: 'user_message', turn_index: 1, branch_id: branch, content: userContent },
    { event_id: 2, type: 'processing_start', turn_index: 1, branch_id: branch },
    { event_id: 3, type: 'text', turn_index: 1, branch_id: branch, content: assistantContent },
    {
      event_id: 4,
      type: 'activity',
      activity_type: 'tool_start',
      turn_index: 1,
      branch_id: branch,
      id: 'tc-1',
      job_id: 'job-1',
      name: 'bash',
      args: { cmd: 'ls -la' },
    },
    {
      event_id: 5,
      type: 'activity',
      activity_type: 'tool_done',
      turn_index: 1,
      branch_id: branch,
      id: 'tc-1',
      job_id: 'job-1',
      name: 'bash',
      result: TOOL_SECRET,
    },
    { event_id: 6, type: 'processing_end', turn_index: 1, branch_id: branch },
  ].map((event) => ({ ...event, event_id: event.event_id + (branch - 1) * 10 }))
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

async function boot({ onEditMessage, onRegenerate, onClipboard } = {}) {
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
  const clipboards = []
  let history = persistedEvents()
  let nextBranch = 1
  let backendProcessing = false
  const session = {
    runtimeId: RUNTIME,
    title: 'Actions Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  let socketGeneration = 0
  let liveSockets = new Set()
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  const fail = (request, error, fields = {}) => receive({ type: 'error', requestId: request.requestId, error, ...fields })
  const idle = () => {
    backendProcessing = false
    receive({ type: 'ws.frame', socketId: [...liveSockets].at(-1), data: JSON.stringify({ type: 'idle', source: CREATURE }) })
  }
  const setHistory = (next) => {
    history = next
    idle()
  }
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      message = structuredClone(message)
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'ready') {
          socketGeneration++
          for (const socketId of liveSockets) receive({ type: 'ws.closed', socketId, code: 1000 })
          liveSockets = new Set()
          return
        }
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.history') reply(message, { events: history, is_processing: backendProcessing })
        if (message.type === 'http.historyPage')
          reply(message, {
            events: history,
            is_processing: backendProcessing,
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
        if (message.type === 'http.editMessage') {
          if (!allowedMessage(message)) return fail(message, 'Invalid branch mutation request', { mayHaveRun: false })
          backendProcessing = true
          const outcome = onEditMessage ? onEditMessage(message, { fail, reply, setHistory }) : null
          if (!outcome?.handled) {
            const text = typeof message.content === 'string' ? message.content : message.content?.[0]?.text || ''
            const branch = ++nextBranch
            history.push(...persistedEvents({ userContent: message.content, assistantContent: `Reran: ${text}`, branch }))
            idle()
            reply(message, { status: 'completed', request_id: message.correlationId, branch_id: branch, turn_index: message.turnIndex })
          }
        }
        if (message.type === 'http.regenerate') {
          if (!allowedMessage(message)) return fail(message, 'Invalid branch mutation request', { mayHaveRun: false })
          backendProcessing = true
          const outcome = onRegenerate ? onRegenerate(message, { fail, reply, setHistory }) : null
          if (!outcome?.handled) {
            const branch = ++nextBranch
            history.push(...persistedEvents({ assistantContent: 'Regenerated.', branch }))
            idle()
            reply(message, { status: 'completed', request_id: message.correlationId, branch_id: branch, turn_index: message.turnIndex })
          }
        }
        if (message.type === 'platform.writeClipboard') {
          clipboards.push(message.text)
          const outcome = onClipboard?.(message, { reply, fail })
          if (!outcome?.handled) reply(message, { written: true })
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
        if (message.type === 'ws.send' && liveSockets.has(message.socketId))
          receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)
  const button = (label) => document.querySelector(`[aria-label="${label}"]`)

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(20)
  const refresh = async () => {
    button('Refresh Sessions').click()
    await settle()
    const request = sent('ready').at(-1)
    reply(request, {
      available: true,
      automatic: true,
      readyId: request.requestId,
      connectionId: 'service-a',
      selectionVersion: 1,
      selection,
    })
    await settle(20)
  }
  return { window, document, sent, clipboards, button, errors, refresh, close: () => window.close() }
}

test('built App renders persisted history with copy buttons and copies the visible text only', async () => {
  const app = await boot()
  try {
    const copy = app.button('Copy message')
    const copyResponse = app.button('Copy response')
    assert.ok(copy, 'the persisted user row rendered its copy control')
    assert.ok(copyResponse, 'the persisted assistant row rendered its copy control')
    assert.ok(app.document.body.textContent.includes(USER_TEXT), 'the persisted user text rendered')
    assert.equal(app.document.querySelector('.kt-transcript-viewport strong')?.textContent, 'Sure thing.', 'assistant Markdown is rendered')

    copy.click()
    await settle()
    assert.equal(app.clipboards.at(-1), USER_TEXT, 'user copy carried the exact message text')

    copyResponse.click()
    await settle()
    assert.equal(app.clipboards.at(-1), ASSISTANT_TEXT, 'assistant copy carried the visible text only, not the tool body')
    assert.ok(!app.clipboards.at(-1).includes(TOOL_SECRET), 'the tool body never reached the clipboard')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App edits a persisted user message and resyncs the real history', async () => {
  const app = await boot()
  try {
    app.button('Edit and rerun message').click()
    await settle(16)
    const textarea = app.document.querySelector('textarea.message-edit-inline')
    assert.ok(textarea, 'the inline editor opened with the retained content')
    assert.equal(textarea.value, USER_TEXT, 'the editor is seeded from the persisted draft')

    textarea.value = 'Edited prompt'
    textarea.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    await settle()
    app.button('Save and rerun').click()
    await settle(24)

    const edits = app.sent('http.editMessage')
    assert.equal(edits.length, 1, 'exactly one edit POST crossed the branch transport')
    // The content array crosses the jsdom realm, so compare its shape fieldwise.
    assert.equal(edits[0].content.length, 1, 'the edit carried one rebuilt part')
    assert.equal(edits[0].content[0].type, 'text')
    assert.equal(edits[0].content[0].text, 'Edited prompt', 'the edit carried the rebuilt content text')
    assert.equal(edits[0].session, RUNTIME)
    assert.equal(edits[0].creature, CREATURE)
    assert.equal(edits[0].turnIndex, 1, 'the edit targeted the persisted turn, not the tail')
    assert.deepEqual(JSON.parse(JSON.stringify(edits[0].locator)), { eventId: 1, turnIndex: 1, branchId: 1 })
    assert.equal(allowedMessage(edits[0]), true)

    assert.ok(app.document.body.textContent.includes('Edited prompt'), 'the resynced history shows the updated user text')
    assert.ok(app.document.body.textContent.includes('Reran: Edited prompt'), 'the resynced history shows the new assistant reply')
    assert.equal(app.document.querySelector('textarea.message-edit-inline'), null, 'the editor closed on success')
    app.button('Previous user edit').click()
    await settle()
    assert.match(app.document.body.textContent, /Hello there/)
    assert.ok(!app.document.body.textContent.includes('Reran: Edited prompt'))
    app.button('Next user edit').click()
    await settle()
    assert.match(app.document.body.textContent, /Reran: Edited prompt/)
    assert.equal(app.sent('http.editMessage').length, 1, 'branch navigation never submits an edit')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App regenerates the historical turn with a single POST', async () => {
  const app = await boot()
  try {
    app.button('Regenerate response').click()
    await settle(24)
    const regens = app.sent('http.regenerate')
    assert.equal(regens.length, 1, 'exactly one regenerate POST crossed the branch transport')
    assert.equal(regens[0].session, RUNTIME)
    assert.equal(regens[0].creature, CREATURE)
    assert.equal(regens[0].turnIndex, 1, 'regenerate targeted the persisted turn, not the tail')
    assert.deepEqual(JSON.parse(JSON.stringify(regens[0].locator)), { eventId: 1, turnIndex: 1, branchId: 1 })
    assert.ok(app.document.body.textContent.includes('Regenerated.'), 'the resynced history shows the regenerated reply')
    app.button('Previous regen').click()
    await settle()
    assert.match(app.document.body.textContent, /Sure thing\./)
    assert.ok(!app.document.body.textContent.includes('Regenerated.'))
    app.button('Next regen').click()
    await settle()
    assert.match(app.document.body.textContent, /Regenerated\./)
    assert.equal(app.sent('http.regenerate').length, 1)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App retains a refused edit draft and attachment for an explicit retry', async () => {
  let attempts = 0
  const app = await boot({
    onEditMessage: (message, { fail }) => {
      if (attempts++ > 0) return null
      // A received 409 is definite even though the Host dispatched the request.
      fail(message, 'This turn is locked', { status: 409, mayHaveRun: true })
      return { handled: true }
    },
  })
  try {
    app.button('Edit and rerun message').click()
    await settle(16)
    const textarea = app.document.querySelector('textarea.message-edit-inline')
    textarea.value = 'Locked edit'
    textarea.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    const input = [...app.document.querySelectorAll('input[type="file"]')].find((element) => !element.accept)
    Object.defineProperty(input, 'files', {
      value: [{ name: 'retry.txt', size: 14, type: 'text/plain', text: async () => 'attached draft' }],
    })
    input.dispatchEvent(new app.window.Event('change', { bubbles: true }))
    await settle()
    app.button('Save and rerun').click()
    await settle(24)

    assert.equal(app.sent('http.editMessage').length, 1, 'the 409 edit was attempted exactly once, never retried')
    const alert = app.document.querySelector('[role="alert"]')
    assert.ok(alert, 'the inline error region rendered')
    assert.match(alert.textContent, /locked/i)
    const restored = app.document.querySelector('textarea.message-edit-inline')
    assert.ok(restored, 'a definite refusal restores the original message editor after replay')
    assert.equal(restored.value, 'Locked edit', 'a refused edit never discards the submitted draft')
    assert.match(restored.closest('.user-message-editing').textContent, /retry\.txt/)
    assert.equal(app.button('Save and rerun').disabled, false)
    app.button('Save and rerun').click()
    await settle(24)
    const retry = app.sent('http.editMessage')[1]
    assert.equal(retry.content[0].text, 'Locked edit')
    assert.equal(retry.content[1].file.name, 'retry.txt')
    assert.equal(retry.content[1].file.content, 'attached draft')
    assert.match(app.document.body.textContent, /Reran: Locked edit/)
    assert.equal(app.document.querySelector('textarea.message-edit-inline'), null)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

async function editForm(app, text, file = null) {
  app.button('Edit and rerun message').click()
  await settle()
  const textarea = app.document.querySelector('textarea.message-edit-inline')
  textarea.value = text
  textarea.dispatchEvent(new app.window.Event('input', { bubbles: true }))
  if (file) {
    const input = [...app.document.querySelectorAll('input[type="file"]')].find((element) => !element.accept)
    Object.defineProperty(input, 'files', { value: [file] })
    input.dispatchEvent(new app.window.Event('change', { bubbles: true }))
  }
  await settle()
  app.button('Save and rerun').click()
  await settle(20)
}

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('Refresh during attachment preparation prevents the old edit and preserves the new composer focus', async () => {
  const app = await boot()
  const fileText = deferred()
  let reads = 0
  try {
    await editForm(app, 'old prepared draft', {
      name: 'delayed.txt',
      size: 4,
      type: 'text/plain',
      text: () => {
        reads++
        return fileText.promise
      },
    })
    assert.equal(reads, 1, 'the real content-parts builder started reading the attachment')
    assert.equal(app.sent('http.editMessage').length, 0)
    await app.refresh()
    const composer = app.document.querySelector('.kt-chat-composer textarea')
    assert.ok(composer)
    composer.focus()
    fileText.resolve('old attachment')
    await settle(24)
    assert.equal(app.sent('http.editMessage').length, 0)
    assert.equal(app.document.activeElement, composer)
    assert.equal(app.document.querySelector('textarea.message-edit-inline'), null)
    assert.match(app.document.body.textContent, /Hello there/)
    assert.ok(!app.document.body.textContent.includes('old prepared draft'))
    assert.deepEqual(app.errors, [])
  } finally {
    fileText.resolve('finish')
    app.close()
  }
})

for (const refreshed of [false, true]) {
  test(`clipboard refusal is visible only on its owning view (Refresh=${refreshed})`, async () => {
    let refuse
    const app = await boot({
      onClipboard: (message, { fail }) => {
        refuse = () => fail(message, 'Could not copy the message to the clipboard.', { code: 'clipboard_write_failed' })
        return { handled: true }
      },
    })
    try {
      app.button('Copy message').click()
      await settle()
      assert.equal(app.clipboards.length, 1)
      assert.equal(app.document.querySelector('.kt-notification-error'), null)
      if (refreshed) await app.refresh()
      refuse()
      await settle()
      assert.equal(!!app.document.querySelector('.kt-notification-error'), !refreshed)
      assert.equal(app.clipboards.length, 1)
      assert.deepEqual(app.errors, [])
    } finally {
      app.close()
    }
  })
}

for (const status of [502, undefined]) {
  test(`an uncertain edit response (${status ?? 'lost'}) retains the branch until history confirms it`, async () => {
    let refuse
    let setHistory
    const app = await boot({
      onEditMessage: (message, transport) => {
        refuse = () => transport.fail(message, 'Outcome not confirmed', { ...(status ? { status } : {}), mayHaveRun: true })
        setHistory = transport.setHistory
        return { handled: true }
      },
    })
    try {
      await editForm(app, 'uncertain edit')
      assert.equal(app.sent('http.editMessage').length, 1)
      assert.equal(app.button('Edit and rerun message').disabled, true)
      assert.match(app.document.body.textContent, /uncertain edit/)
      refuse()
      await settle()
      assert.match(app.document.body.textContent, /uncertain edit/)
      assert.equal(app.document.querySelector('textarea.message-edit-inline'), null)
      setHistory([
        ...persistedEvents(),
        ...persistedEvents({ userContent: 'uncertain edit', assistantContent: 'Confirmed after readback', branch: 2 }),
      ])
      await new Promise((resolve) => setTimeout(resolve, 450))
      await settle(24)
      assert.match(app.document.body.textContent, /Confirmed after readback/)
      assert.equal(app.sent('http.editMessage').length, 1, 'history resync never retries the POST')
      assert.equal(app.button('Edit and rerun message').disabled, false)
      assert.deepEqual(app.errors, [])
    } finally {
      app.close()
    }
  })
}

for (const status of [200, 409]) {
  test(`a delayed ${status} edit response after Refresh cannot reopen an old draft`, async () => {
    let finish
    const app = await boot({
      onEditMessage: (message, { fail, reply }) => {
        finish = () =>
          status === 200
            ? reply(message, { status: 'completed', request_id: message.correlationId, turn_index: 1, branch_id: 2 })
            : fail(message, 'old conflict', { status, mayHaveRun: true })
        return { handled: true }
      },
    })
    try {
      await editForm(app, 'stale submitted draft')
      await app.refresh()
      const composer = app.document.querySelector('.kt-chat-composer textarea')
      composer.focus()
      finish()
      await settle(24)
      assert.equal(app.document.activeElement, composer)
      assert.equal(app.document.querySelector('textarea.message-edit-inline'), null)
      assert.ok(!app.document.body.textContent.includes('old conflict'))
      assert.ok(!app.document.body.textContent.includes('stale submitted draft'))
      assert.equal(app.sent('http.editMessage').length, 1)
      assert.deepEqual(app.errors, [])
    } finally {
      app.close()
    }
  })
}
