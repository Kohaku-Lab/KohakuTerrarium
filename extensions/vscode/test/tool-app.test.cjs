// End-to-end TOOL workflow through the REAL built VS Code webview: built bundle
// -> shared chat store -> the one production ToolCallBlock/ToolCallBatch and the
// nested SubagentConversationPanel that the Dashboard also renders, wired to the
// real Host sub-agent bridge (subagentBridge -> shims/api.js). The Host is a
// postMessage fixture (the composer-refresh pattern) that answers the fixed
// ``ready``/socket envelopes AND the fixed ``http.subagent*``/``http.promote``
// routes the bridge installs, so this pins the WORKFLOW the webview drives —
// args, result parts, pinned media, task promotion, and a live sub-agent read +
// send — not a component's initial shape.
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

const RUNTIME = 'graph-live-tool'
const CREATURE = 'alpha'
// A canonical same-origin session-artifact reference, the one shape the shared
// media leaves accept as a displayable tool product.
const ARTIFACT = '/api/sessions/graph_1/artifacts/generated_images/one.png'

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

async function boot() {
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
    title: 'Tool Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  // The Host's live sub-agent conversation read payload (raw backend shape).
  const subagentConversation = {
    can_receive: true,
    messages: [
      { role: 'user', content: 'hi there' },
      {
        role: 'assistant',
        content: 'the answer',
        tool_calls: [{ id: 'call-1', function: { name: 'grep', arguments: '{"pattern":"x"}' } }],
      },
      { role: 'tool', tool_call_id: 'call-1', content: 'match' },
    ],
  }
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
        // The fixed Host routes the sub-agent bridge installs.
        if (message.type === 'http.subagentConversation') reply(message, subagentConversation)
        if (message.type === 'http.subagentSend') reply(message, { ok: true })
        if (message.type === 'http.promote') reply(message, { ok: true, status: 'promoted' })
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
  const activeSocket = () => sent('ws.open').at(-1)?.socketId
  const frame = (data) => receive({ type: 'ws.frame', socketId: activeSocket(), data: JSON.stringify({ source: CREATURE, ...data }) })
  const toolBlock = (label) => document.querySelector(`[aria-label="${label}"]`)?.parentElement || null

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(14)
  return { window, document, frame, sent, toolBlock, errors, close: () => window.close() }
}

test('built App renders the shared production tool block with args, result parts and pinned media, and promotes a running task through the Host', async () => {
  const app = await boot()
  try {
    // A running direct task with args, promotable (not backgrounded).
    app.frame({
      type: 'activity',
      activity_type: 'tool_start',
      id: 'tc-1',
      job_id: 'job-1',
      name: 'bash',
      args: { cmd: 'ls -la' },
      background: false,
    })
    await settle()
    const bash = app.toolBlock('Tool bash')
    assert.ok(bash, 'the shared ToolCallBlock rendered for the started tool')
    // Args come from the real frame, formatted by the production leaf.
    assert.match(bash.textContent, /cmd=ls -la/)

    // The promote control appears for a still-running, promotable task; the
    // real click drives the Host promote route through the installed bridge.
    await new Promise((resolve) => setTimeout(resolve, 1050))
    const promote = bash.querySelector('[aria-label="Move task to background"]')
    assert.ok(promote, 'the shared block exposes the background-promotion control')
    promote.click()
    await settle(20)
    const promoteRequest = app.sent('http.promote').at(-1)
    assert.ok(promoteRequest, 'the promotion crossed the real Host bridge')
    assert.equal(promoteRequest.session, RUNTIME)
    assert.equal(promoteRequest.creature, CREATURE)
    assert.equal(promoteRequest.jobId, 'job-1')

    // Result parts (Markdown text) and media (a pinned session artifact) land on
    // the same production block.
    app.frame({
      type: 'activity',
      activity_type: 'tool_done',
      id: 'tc-1',
      job_id: 'job-1',
      name: 'bash',
      result: [
        { type: 'text', text: 'done listing' },
        { type: 'image_url', image_url: { url: ARTIFACT } },
      ],
      result_meta: { media: { pinned: true } },
    })
    await settle()
    assert.ok(bash.querySelector('[data-testid="tool-media-pinned"] img'), 'the pinned tool media rendered')
    assert.equal(
      bash.querySelector('[data-testid="tool-media-pinned"] img').getAttribute('src'),
      ARTIFACT,
      'the pinned media resolves to the session-artifact reference',
    )
    // Expand to reach the raw result parts (text) kept behind the accordion.
    bash.querySelector('[aria-label="Tool bash"]').click()
    await settle()
    assert.match(bash.textContent, /done listing/)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App reads the nested live sub-agent conversation through the Host bridge and sends to the live run', async () => {
  const app = await boot()
  try {
    app.frame({
      type: 'activity',
      activity_type: 'subagent_start',
      id: 'tc-sa',
      job_id: 'sa-1',
      name: 'researcher',
      args: { task: 'investigate' },
      llm_name: 'gpt-test',
      model: 'gpt-test',
    })
    await settle()
    const sub = app.toolBlock('Sub-agent researcher')
    assert.ok(sub, 'the shared sub-agent block rendered')

    // Expand the sub-agent, then open its inner conversation surface.
    sub.querySelector('[aria-label="Sub-agent researcher"]').click()
    await settle()
    const conversation = [...sub.querySelectorAll('button')].find((node) => node.textContent.includes('Conversation'))
    assert.ok(conversation, 'the nested conversation disclosure rendered')
    conversation.click()
    await settle(20)

    // The shared panel resolved the live read through the real Host bridge.
    const read = app.sent('http.subagentConversation').at(-1)
    assert.ok(read, 'the nested conversation read crossed the Host bridge')
    assert.equal(read.session, RUNTIME)
    assert.equal(read.creature, CREATURE)
    // Cross-realm (jsdom) object: copy into this realm before a strict compare.
    assert.deepEqual({ ...read.options }, { jobId: 'sa-1', name: 'researcher' })

    const panel = sub.querySelector('[data-test="subagent-messages"]')
    assert.ok(panel, 'the shared SubagentConversationPanel rendered')
    assert.match(panel.textContent, /hi there/)
    assert.match(panel.textContent, /the answer/)

    // A live run accepts a message: type + send through the same bridge.
    const textarea = sub.querySelector('textarea')
    assert.ok(textarea, 'the live run exposes the send composer')
    textarea.value = 'ping'
    textarea.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    await settle()
    const sendButton = [...sub.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Send')
    assert.ok(sendButton, 'the send control rendered for the live run')
    sendButton.click()
    await settle(20)

    const sent = app.sent('http.subagentSend').at(-1)
    assert.ok(sent, 'the sub-agent send crossed the Host bridge')
    assert.equal(sent.session, RUNTIME)
    assert.equal(sent.creature, CREATURE)
    assert.equal(sent.name, 'researcher')
    assert.equal(sent.content, 'ping')
    assert.equal(sent.jobId, 'sa-1')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})
