// End-to-end SLASH workflow through the REAL built VS Code webview: built bundle
// -> the one production SlashCommandMenu/useSlashCommandCompletion (shared with
// the Dashboard through the public @kohakuterrarium/chat-ui seam) -> the real
// chat store's command-inventory cache and the real Host bridges (modelBridge /
// goalBridge -> shims/api.js). The Host is a postMessage fixture that answers the
// fixed ready/socket envelopes AND the fixed http.commandInventory + goal.execute
// routes, so this pins the WORKFLOW the webview drives — open/loading, search by
// name and alias, keyboard + mouse completion, Enter-choose without dispatch,
// goal-only HTTP dispatch through the goal bridge, ordinary skills over the WS,
// a visible failure instead of a fake empty — not a component's shape.
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

const RUNTIME = 'graph-live-slash'
const CREATURE = 'alpha'

// The live inventory: one visible /goal (with an alias), hidden non-goal
// commands the menu must never advertise, one enabled skill, plus a disabled, an
// invocation-blocked and a namespace-colliding skill that must all stay hidden.
const INVENTORY = {
  commands: [
    { name: 'goal', aliases: ['g'], description: 'Manage goals' },
    { name: 'status', aliases: ['info'], description: 'Show status' },
    { name: 'compact', aliases: [], description: 'Compact context' },
  ],
  skills: [
    { name: 'research', enabled: true, description: 'Research a topic' },
    { name: 'disabled-review', enabled: false, description: 'Off' },
    { name: 'manual-only', enabled: true, invocation_blocked: true, description: 'Manual' },
    { name: 'status', enabled: true, description: 'Colliding skill' },
  ],
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

async function boot({ holdInventory = false, inventoryError = null } = {}) {
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
    title: 'Slash Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  const selection = { session: RUNTIME, targetCreatureId: 'creature-a' }
  const heldInventory = []
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
        // The fixed Host command-inventory route the model bridge installs.
        if (message.type === 'http.commandInventory') {
          if (holdInventory) heldInventory.push(message)
          else if (inventoryError) receive({ type: 'error', requestId: message.requestId, error: inventoryError, status: 500 })
          else reply(message, INVENTORY)
        }
        // The fixed Host goal route the goal bridge installs.
        if (message.type === 'goal.execute')
          reply(message, { command: 'goal', success: true, output: `goal ${message.args || '(none)'}`, error: '' })
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
        if (message.type === 'ws.send' && JSON.parse(message.data).type === 'input')
          receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)
  const textarea = () => document.querySelector('.composer-region textarea')
  const options = () => [...document.querySelectorAll('#slash-command-menu button[role="option"]')]
  const type = (text) => {
    const node = textarea()
    node.value = text
    node.dispatchEvent(new window.Event('input', { bubbles: true }))
  }
  const press = (key, init = {}) => {
    const event = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
    textarea().dispatchEvent(event)
    return event
  }

  window.eval(code)
  await settle()
  const ready = requests.find((message) => message.type === 'ready')
  reply(ready, { available: true, automatic: true, readyId: ready.requestId, connectionId: 'service-a', selectionVersion: 1, selection })
  await settle(16)
  return {
    window,
    document,
    sent,
    textarea,
    options,
    type,
    press,
    releaseInventory: (data = INVENTORY) => heldInventory.splice(0).forEach((request) => reply(request, data)),
    failInventory: (error = 'inventory unavailable') =>
      heldInventory.splice(0).forEach((request) => receive({ type: 'error', requestId: request.requestId, error, status: 500 })),
    errors,
    close: () => window.close(),
  }
}

test('the built webview opens the shared production menu, searches by name/alias, and completes by keyboard and mouse without dispatching', async () => {
  const app = await boot({ holdInventory: true })
  try {
    // A real slash query opens the menu and shows the loading state until the
    // Host inventory answers.
    app.type('/')
    await settle()
    const menu = app.document.querySelector('#slash-command-menu')
    assert.ok(menu, 'the shared SlashCommandMenu rendered for a slash query')
    assert.match(menu.textContent, /Loading commands and skills/, 'the menu exposes a real loading state')
    assert.equal(app.sent('http.commandInventory').length, 1, 'the slash query loaded the real inventory through the Host bridge')
    const request = app.sent('http.commandInventory')[0]
    assert.equal(request.session, RUNTIME)
    assert.equal(request.creature, CREATURE)

    app.releaseInventory()
    await settle()
    // Only the visible /goal command and the enabled, non-colliding skill show.
    assert.deepEqual(
      app.options().map((node) => [...node.children].map((child) => child.textContent.trim()).join(' ')),
      ['/goal Manage goals', '/research Research a topic'],
      'hidden non-goal commands and unavailable skills stay out of the menu',
    )

    // Keyboard navigation moves the ARIA active descendant in grouped visual order.
    const textarea = app.textarea()
    assert.equal(textarea.getAttribute('role'), 'combobox')
    assert.equal(textarea.getAttribute('aria-autocomplete'), 'list')
    assert.equal(textarea.getAttribute('aria-expanded'), 'true')
    assert.equal(textarea.getAttribute('aria-controls'), 'slash-command-menu')
    assert.equal(textarea.getAttribute('aria-activedescendant'), 'slash-option-0')
    app.press('ArrowDown')
    await settle()
    assert.equal(app.textarea().getAttribute('aria-activedescendant'), 'slash-option-1')
    app.press('ArrowUp')
    await settle()
    assert.equal(app.textarea().getAttribute('aria-activedescendant'), 'slash-option-0')

    // First Enter chooses the completion and must NOT dispatch anything.
    app.press('Enter')
    await settle()
    assert.equal(app.textarea().value, '/goal ', 'Enter selected the highlighted completion')
    assert.equal(app.sent('ws.send').length, 0, 'choosing a completion never dispatched a chat send')
    assert.equal(app.sent('goal.execute').length, 0, 'choosing a completion never executed a command')

    // Search by alias narrows to the visible command; mouse click completes it.
    app.type('/g')
    await settle()
    assert.deepEqual(
      app.options().map((node) => [...node.children].map((child) => child.textContent.trim()).join(' ')),
      ['/goal Manage goals'],
    )
    app.options()[0].click()
    await settle()
    assert.equal(app.textarea().value, '/goal ')
    assert.equal(app.sent('ws.send').length, 0)

    // Search by skill name and complete it with the mouse.
    app.type('/res')
    await settle()
    assert.deepEqual(
      app.options().map((node) => node.textContent.includes('/research')),
      [true],
    )
    app.options()[0].click()
    await settle()
    assert.equal(app.textarea().value, '/research ')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('Enter after completion submits the goal command through the real goal bridge with its exact args', async () => {
  const app = await boot()
  try {
    app.type('/go')
    await settle()
    assert.equal(
      app.options().some((node) => node.textContent.includes('/goal')),
      true,
      'the menu shows /goal',
    )
    app.options()[0].click()
    await settle()
    // Add an argument after the completion, then submit with Enter.
    app.type('/goal release the mice')
    await settle()
    app.press('Enter')
    await settle(16)

    const goal = app.sent('goal.execute').at(-1)
    assert.ok(goal, 'the goal command crossed the real Host bridge')
    assert.equal(goal.args, 'release the mice')
    assert.equal(app.sent('ws.send').length, 0, 'the goal command never went over the chat socket')
    assert.match(app.document.body.textContent, /goal release the mice/, 'the goal result rendered through the shared command-result leaf')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('an ordinary skill sends over the socket with Host acceptance while a hidden non-goal command stays ordinary text', async () => {
  const app = await boot()
  try {
    app.type('/res')
    await settle()
    app.options()[0].click()
    await settle()
    assert.equal(app.textarea().value, '/research ')
    app.press('Enter')
    await settle(16)

    const send = app.sent('ws.send').at(-1)
    assert.ok(send, 'the skill send crossed the chat socket')
    const frame = JSON.parse(send.data)
    assert.equal(frame.type, 'input')
    assert.equal(frame.target, CREATURE)
    assert.deepEqual(frame.content, [{ type: 'text', text: '/research ' }])
    assert.equal(app.sent('goal.execute').length, 0, 'an ordinary skill never reaches the goal HTTP route')
    assert.equal(app.textarea().value, '', 'the composer cleared after Host acceptance')

    // A hidden non-goal command the inventory advertises is NOT authorized: it
    // follows the existing ordinary WS input path, never a synthesized HTTP route.
    const before = app.sent('ws.send').length
    app.type('/status now')
    await settle()
    app.press('Enter')
    await settle(16)
    assert.equal(app.sent('ws.send').length, before + 1, 'the hidden command went over the socket as ordinary text')
    assert.equal(JSON.parse(app.sent('ws.send').at(-1).data).content[0].text, '/status now')
    assert.equal(app.sent('goal.execute').length, 0)
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('a failed inventory shows a truthful visible error instead of a fake empty, and only an explicit edit retries', async () => {
  const app = await boot({ inventoryError: 'inventory unavailable' })
  try {
    app.type('/')
    await settle()
    const menu = app.document.querySelector('#slash-command-menu')
    assert.ok(menu, 'the menu surfaced the failure')
    assert.doesNotMatch(menu.textContent, /No matching commands or skills/, 'a failure must not masquerade as an empty result')
    assert.match(menu.textContent, /Could not load commands and skills/, 'the failure is shown through the shared dictionary')
    assert.equal(app.sent('http.commandInventory').length, 1, 'a failed load does not busy-loop')

    // Only an explicit edit retries the load.
    app.type('/g')
    await settle()
    assert.equal(app.sent('http.commandInventory').length, 2, 'editing the query retried the inventory exactly once')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('manual slash sends retain Dashboard fallback when command inventory is unavailable', async () => {
  const app = await boot({ inventoryError: 'inventory unavailable' })
  try {
    app.type('/goal list')
    await settle()
    app.press('Enter')
    await settle(16)
    assert.equal(app.sent('http.commandInventory').length, 1)
    assert.equal(app.sent('goal.execute').length, 1, 'literal goal still reaches the existing command route')
    assert.equal(app.sent('goal.execute')[0].args, 'list')
    assert.equal(app.sent('ws.send').length, 0)
    assert.equal(app.textarea().value, '')

    app.type('/status now')
    await settle()
    app.press('Enter')
    await settle(16)
    assert.equal(app.sent('http.commandInventory').length, 2, 'one lookup per explicit send, no automatic retry')
    assert.equal(app.sent('goal.execute').length, 1, 'fallback does not expand command execution')
    assert.equal(app.sent('ws.send').length, 1)
    const frame = JSON.parse(app.sent('ws.send')[0].data)
    assert.equal(frame.target, CREATURE)
    assert.equal(frame.content[0].text, '/status now')
    assert.equal(app.textarea().value, '')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('inventory failure after Refresh never falls back onto a superseding conversation', async () => {
  const app = await boot({ holdInventory: true })
  try {
    app.type('/goal list')
    await settle()
    app.press('Enter')
    await settle()
    assert.equal(app.sent('http.commandInventory').length, 1)
    app.document.querySelector('button[aria-label="Refresh Sessions"]').click()
    await settle()
    assert.equal(app.sent('ready').length, 2)
    app.failInventory()
    await settle(16)
    assert.equal(app.sent('goal.execute').length, 0)
    assert.equal(app.sent('ws.send').length, 0)
    assert.equal(app.textarea().value, '/goal list', 'the superseded draft remains owned by its conversation')
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})

test('IME composition preserves the draft before ordinary Enter completes', async () => {
  const app = await boot({ holdInventory: true })
  try {
    app.type('/')
    await settle()
    assert.equal(app.sent('http.commandInventory').length, 1, 'the slash query loaded the real inventory')
    app.releaseInventory()
    await settle()
    assert.equal(
      app.options().some((node) => node.textContent.includes('/goal')),
      true,
      'the menu shows /goal',
    )

    // A composing Enter belongs to the input method, never the menu.
    app.press('Enter', { isComposing: true, keyCode: 229 })
    await settle()
    assert.equal(app.textarea().value, '/', 'a composing Enter did not complete the completion')
    assert.equal(app.sent('ws.send').length, 0, 'a composing Enter never dispatched a chat send')
    assert.equal(app.sent('goal.execute').length, 0, 'a composing Enter never executed a command')

    // The next real Enter completes the highlighted /goal without dispatching.
    app.press('Enter')
    await settle()
    assert.equal(app.textarea().value, '/goal ', 'a normal Enter then completed the highlighted entry')
    assert.equal(app.sent('ws.send').length, 0)
    assert.equal(app.sent('goal.execute').length, 0)
    assert.deepEqual(app.errors, [])
  } finally {
    app.close()
  }
})
