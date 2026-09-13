// End-to-end command-result rendering through the REAL built VS Code webview:
// built bundle -> shim -> installGoalBridge -> shared chat store -> the same
// production CommandResultMessage the Dashboard renders. Pins the structured
// info_panel / list / inline error / localized empty completion, the shared
// i18n provider seam (actual dictionaries, not the sparse identity shim), the
// host-language locale selection, and the warm surface + Carbon icon CSS the
// shared component depends on.
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

const RUNTIME = 'graph-live'
const TAB = 'alpha'

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
  const asText = (item) => (typeof item.source === 'string' ? item.source : Buffer.from(item.source).toString('utf8'))
  cachedBundle = {
    code: outputs.find((item) => item.type === 'chunk' && item.isEntry).code,
    css: outputs
      .filter((item) => item.type === 'asset' && /\.css$/.test(item.fileName))
      .map(asText)
      .join('\n'),
  }
  return cachedBundle
}

async function settle(n = 10) {
  for (let i = 0; i < n; i++) await new Promise((resolve) => setImmediate(resolve))
}

async function bootApp({ goalResult, lang = 'en', theme = '', viewportWidth = 0 }) {
  const { code } = await buildWebview()
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
  // The webview derives its production layout density from window.innerWidth.
  if (viewportWidth) Object.defineProperty(window, 'innerWidth', { configurable: true, value: viewportWidth })
  // The extension stamps the active VS Code theme onto <body>; the webview mirrors it.
  if (theme) document.body.className = theme === 'dark' ? 'vscode-dark' : 'vscode-light'
  // The extension writes the host language (``vscode.env.language``) into the
  // document tag the webview reads as its locale seam.
  document.documentElement.setAttribute('lang', lang)
  const requests = []
  const session = {
    runtimeId: RUNTIME,
    title: 'Command Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: TAB }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.history') reply(message, { events: [] })
        if (message.type === 'http.historyPage')
          reply(message, {
            events: [],
            messages: [],
            history_page: {
              version: 1,
              stream: 'events',
              history_id: 'history',
              has_older: false,
              has_newer: false,
              reset_required: false,
            },
          })
        if (message.type === 'goal.execute') reply(message, goalResult)
        if (message.type === 'ws.open') receive({ type: 'ws.opened', socketId: message.socketId })
        if (message.type === 'ws.close') receive({ type: 'ws.closed', socketId: message.socketId })
      })
    },
  })

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  const readyId = ready.requestId
  reply(ready, { available: true, automatic: true, readyId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(12)

  const transcriptText = () => document.querySelector('.kt-transcript-section')?.textContent || ''
  const runGoal = async (args) => {
    await window.__ktVsCodeGoal(RUNTIME, TAB, args)
    await settle(12)
  }
  return { window, document, requests, transcriptText, runGoal, errors, close: () => window.close() }
}

test('built App renders the shared command-result component for goal output', async () => {
  const app = await bootApp({
    goalResult: {
      success: true,
      output: '',
      data: {
        type: 'info_panel',
        title: 'Goal',
        fields: [
          { key: 'status', value: 'active' },
          { key: 'owner', value: 'user:local' },
        ],
      },
    },
  })
  try {
    await app.runGoal('show')
    const text = app.transcriptText()
    assert.match(text, /\/goal show/, 'the command header renders')
    assert.match(text, /Goal/, 'the structured title renders')
    assert.match(text, /status/)
    assert.match(text, /active/)
    assert.match(text, /user:local/)
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('built App renders a structured list command result', async () => {
  const app = await bootApp({
    goalResult: {
      success: true,
      output: 'ignored raw fallback',
      data: {
        type: 'list',
        title: 'Goals',
        items: [
          { label: 'Ship release', description: 'id=drive_1' },
          { label: 'Write docs', description: 'id=drive_2' },
        ],
      },
    },
  })
  try {
    await app.runGoal('list')
    const text = app.transcriptText()
    assert.match(text, /Ship release/)
    assert.match(text, /id=drive_2/)
    assert.doesNotMatch(text, /ignored raw fallback/, 'structured output wins over the raw fallback')
  } finally {
    app.close()
  }
})

test('built App renders an inline command error', async () => {
  const app = await bootApp({ goalResult: { success: false, output: '', error: 'usage: /goal set <objective>' } })
  try {
    // The bridge surfaces the result into the transcript and then rejects, so
    // the caller sees the failure while the UI keeps the inline error.
    await assert.rejects(app.runGoal('set'), /usage: \/goal set <objective>/)
    assert.match(app.transcriptText(), /usage: \/goal set <objective>/)
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('built App localizes the empty completion through the shared dictionary provider', async () => {
  const app = await bootApp({ goalResult: { success: true, output: '' } })
  try {
    await app.runGoal('clear')
    const text = app.transcriptText()
    assert.match(text, /Command completed\./, 'the real dictionary string renders')
    assert.doesNotMatch(text, /chat\.command\.completed/, 'the identity shim must not leak the raw key')
  } finally {
    app.close()
  }
})

test('built App localizes through the host language (case-insensitive BCP-47 tag)', async () => {
  // ``vscode.env.language`` emits lower-case tags (``zh-cn``); the shared store
  // must resolve the primary subtag to the real zh-CN dictionary.
  const app = await bootApp({ goalResult: { success: true, output: '' }, lang: 'zh-cn' })
  try {
    await app.runGoal('clear')
    assert.match(app.transcriptText(), /命令已完成。/, 'the host language selects the real dictionary')
  } finally {
    app.close()
  }
})

test('built webview emits the shared warm surface and Carbon icon utility CSS', async () => {
  const { css } = await buildWebview()
  assert.ok(css.length > 0, 'the bundle emitted aggregated CSS')
  // Both literals come from the production CommandResultMessage/uno.config.js
  // graph; their presence proves the shared utility and icon atoms survive.
  assert.match(css, /\.bg-warm-50/, 'the warm surface utility from uno.config.js is generated')
  assert.match(css, /\.i-carbon-terminal/, 'the Carbon terminal icon atom used by the shared component is generated')
  assert.match(css, /\.dark \.dark\\:bg-warm-800/, 'the dark surface variant from uno.config.js is generated')
})

test('built webview bundles the genuine Element Plus dark theme provider', async () => {
  const { css } = await buildWebview()
  // The shared drawer/select/popper surfaces are Element Plus widgets: without
  // the installed dark css-vars a dark host leaves them on the light defaults.
  assert.match(css, /html\.dark\{[^}]*--el-bg-color/, 'the installed Element Plus dark css-vars are bundled')
})

test('built App mirrors the VS Code dark host theme onto <html>', async () => {
  const app = await bootApp({ goalResult: { success: true, output: '' }, theme: 'dark' })
  try {
    assert.equal(app.document.documentElement.classList.contains('dark'), true, 'the first paint is already themed')
    assert.deepEqual(app.errors, [], 'theming adds no runtime errors')
  } finally {
    app.close()
  }
})

test('built App collapses the composer to the compact more-menu at narrow width and keeps Enter-to-send', async () => {
  const app = await bootApp({ goalResult: { success: true, output: '' }, viewportWidth: 320 })
  try {
    const ta = app.document.querySelector('.composer-region textarea')
    assert.ok(ta, 'the shared composer renders')
    ta.focus()
    ta.value = 'hello'
    ta.dispatchEvent(new app.window.Event('input', { bubbles: true }))
    await settle()
    assert.ok(app.document.querySelector('[aria-label="More actions"]'), 'the compact more control renders at narrow density')
    assert.equal(app.document.querySelector('[aria-label="Attach file"]'), null, 'standalone attach collapses into the menu')

    // The host keeps the desktop Enter-to-send contract even though the chrome
    // is compact: only the presentation collapses, not the submit key.
    const sends = () => app.requests.filter((message) => message.type === 'ws.send' && /"type":"input"/.test(String(message.data)))
    const before = sends().length
    ta.dispatchEvent(new app.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await settle()
    assert.equal(sends().length, before + 1, 'plain Enter still submits a WS input frame at compact width')
    assert.deepEqual(app.errors, [], 'the compact composer adds no runtime errors')
  } finally {
    app.close()
  }
})

test('built App follows a live VS Code host theme switch', async () => {
  const app = await bootApp({ goalResult: { success: true, output: '' }, theme: 'light' })
  try {
    assert.equal(app.document.documentElement.classList.contains('dark'), false)
    app.document.body.className = 'vscode-high-contrast'
    await settle()
    assert.equal(app.document.documentElement.classList.contains('dark'), true, 'high-contrast dark switches on')
    app.document.body.className = 'vscode-high-contrast-light'
    await settle()
    assert.equal(app.document.documentElement.classList.contains('dark'), false, 'high-contrast light switches off')
  } finally {
    app.close()
  }
})
