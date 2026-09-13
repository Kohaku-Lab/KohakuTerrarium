// End-to-end media workflow through the REAL built VS Code webview: built bundle
// -> shared chat store -> injected Host media resolver -> the production shared
// media leaves (MediaImage, VideoFilePreview, MarkdownRenderer). The Host is a
// postMessage fixture (the composer-refresh pattern): it answers the fixed
// ``media.*`` envelopes exactly as the Runtime coordinator does, so this test
// pins the WORKFLOW the webview drives, not a component's initial shape.
//
// Honesty note: JSDOM has no media pipeline, so no playback is faked. A codec
// failure is the honest ``error`` event a real <video> raises; the preview must
// keep its actions so the media is never stranded.
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

const RUNTIME = 'runtime-media'
const CREATURE = 'alpha'
const ARTIFACT = '/api/sessions/graph_1/artifacts/generated_images/one.png'
// A ``file://`` reference carrying unicode and the reserved ``#?%`` characters:
// the decoded raw path is what the Host's one fixed raw-file route must stream.
const FILE_IMAGE = 'file:///C:/kt%20clips/a%23b%3Fc%25d%20%C3%A9.png'
const FILE_IMAGE_PATH = 'C:/kt clips/a#b?c%d é.png'
const FILE_VIDEO = 'file:///C:/kt%20clips/a%23b%3Fc%25d%20%C3%A9.mp4'
const FILE_VIDEO_PATH = 'C:/kt clips/a#b?c%d é.mp4'

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

async function settle(n = 10) {
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
    title: 'Media Session',
    kind: 'biome',
    isLive: true,
    creatures: [{ id: 'creature-a', name: CREATURE }],
  }
  let selectionVersion = 1
  let prepareSeq = 0
  let holdPrepare = false
  // Real-Host socket bookkeeping. A new ready epoch closes every socket from the
  // previous generation, so the webview must open a FRESH socket; a send on a
  // stale socket id is refused — the epoch fence is never relaxed for the test.
  let socketGeneration = 0
  let liveSockets = new Set()
  // The Host persists live events; a Refresh refetches them as history instead of
  // assuming unsaved webview state survives the new epoch.
  const history = []
  const selection = (targetId = 'creature-a') => ({ session: RUNTIME, targetCreatureId: targetId })
  const receive = (data) => window.dispatchEvent(new window.MessageEvent('message', { data }))
  const reply = (request, data) => receive({ type: `${request.type}.result`, requestId: request.requestId, data })
  const answerPrepare = (request, overrides = {}) => {
    const seq = ++prepareSeq
    const resourceId = overrides.resourceId || `res-${seq}`
    const data = {
      resourceId,
      // Unique per read: a stale read and a fresh read must never share a URI, so
      // a superseded spooled resource can never be mistaken for the live one.
      uri: `vscode-webview://spool/${resourceId}.png`,
      bytes: 12,
      mime: 'video/mp4',
      sha256: 'a'.repeat(64),
      name: request.name || 'media',
      state: 'exposed',
      ...overrides,
    }
    reply(request, data)
    return data
  }
  // The Runtime coordinator rejects a failed prepare with the shared ``error``
  // envelope (requestDemux maps it back to a rejected promise).
  const failPrepare = (request, message = 'artifact read failed', status = 500) =>
    receive({ type: 'error', requestId: request.requestId, error: message, status })
  window.acquireVsCodeApi = () => ({
    postMessage(message) {
      requests.push(message)
      queueMicrotask(() => {
        if (message.type === 'ready') {
          // The Host begins a new ready epoch by closing the previous generation's
          // sockets before it reconciles; the webview's next connection is fresh.
          socketGeneration++
          for (const socketId of liveSockets) receive({ type: 'ws.closed', socketId, code: 1000 })
          liveSockets = new Set()
          return
        }
        if (message.type === 'session.list') reply(message, [session])
        if (message.type === 'http.history') reply(message, { events: [...history] })
        if (message.type === 'http.historyPage')
          reply(message, {
            events: [...history],
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
        if (message.type === 'media.prepare' && !holdPrepare) answerPrepare(message)
        if (
          message.type === 'media.release' ||
          message.type === 'media.cancel' ||
          message.type === 'media.open' ||
          message.type === 'media.save'
        )
          reply(message, {})
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
        if (message.type === 'ws.send') {
          if (!liveSockets.has(message.socketId)) {
            receive({ type: 'ws.send.error', socketId: message.socketId, sendId: message.sendId, error: 'Chat socket is not open' })
            return
          }
          if (JSON.parse(message.data).type === 'input')
            receive({ type: 'ws.send.result', socketId: message.socketId, sendId: message.sendId })
        }
      })
    },
  })
  const sent = (type) => requests.filter((message) => message.type === type)
  const prepares = () => sent('media.prepare')
  const activeSocket = () => sent('ws.open').at(-1)?.socketId
  const frame = (data) => {
    // The backend persists a live event; a Refresh refetches it as history rather
    // than expecting unsaved webview state to survive the new epoch.
    if (data.type === 'image')
      history.push({
        event_id: history.length + 1,
        type: 'assistant_image',
        url: data.url,
        source_type: data.meta?.source_type,
        source_name: data.meta?.source_name,
        revised_prompt: data.meta?.revised_prompt,
      })
    receive({ type: 'ws.frame', socketId: activeSocket(), data: JSON.stringify({ source: CREATURE, ...data }) })
  }
  const answerReady = async (request, targetId = 'creature-a', connectionId = 'service-a') => {
    reply(request, {
      available: true,
      automatic: true,
      readyId: request.requestId,
      connectionId,
      selectionVersion: ++selectionVersion,
      selection: selection(targetId),
    })
    await settle(12)
  }
  window.eval(code)
  await settle()
  await answerReady(requests.find((message) => message.type === 'ready'))
  return {
    window,
    document,
    frame,
    receive,
    answerReady,
    prepares,
    sent,
    answerPrepare,
    failPrepare,
    holdPrepare: (value) => (holdPrepare = value),
    errors,
    close: () => window.close(),
  }
}

test('built App prepares the fixed raw path for a file:// image and the production video preview, and open/save address the Host resource', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    // An assistant image whose source is a local file:// reference…
    app.frame({ type: 'image', url: FILE_IMAGE })
    // …and the single production VideoFilePreview fed by a local file:// reference.
    app.frame({
      type: 'user_input',
      content: [
        { type: 'text', text: 'see clip' },
        { type: 'file', file: { path: FILE_VIDEO, name: 'clip.mp4', mime: 'video/mp4' } },
      ],
    })
    await settle()
    assert.deepEqual(
      app
        .prepares()
        .map((message) => message.path)
        .sort(),
      [FILE_IMAGE_PATH, FILE_VIDEO_PATH].sort(),
      'both the image and the video preview prepare the decoded local raw path, not the file:// scheme',
    )
    app.answerPrepare(app.prepares().find((message) => message.path === FILE_IMAGE_PATH))
    app.answerPrepare(
      app.prepares().find((message) => message.path === FILE_VIDEO_PATH),
      { resourceId: 'host-video' },
    )
    await settle()
    const video = app.document.querySelector('video')
    assert.ok(video, 'the production video preview rendered')
    assert.match(video.getAttribute('src'), /^vscode-webview:\/\/spool\//)
    const actions = video.parentElement
    const open = [...actions.querySelectorAll('button')].find((button) => button.textContent.trim() === 'Open')
    const save = [...actions.querySelectorAll('button')].find((button) => button.textContent.trim() === 'Save')
    assert.ok(open && save, 'the Host resolver exposes explicit open/save controls')
    open.click()
    await settle()
    save.click()
    await settle()
    // Open/save carry the shared Host resourceId, never the per-consumer lease handle.
    assert.equal(app.sent('media.open').at(-1).resourceId, 'host-video')
    assert.equal(app.sent('media.save').at(-1).resourceId, 'host-video')
    assert.equal(
      app.sent('media.open').some((message) => String(message.resourceId).startsWith('kt-lease')),
      false,
      'the webview lease handle never crosses the transport',
    )
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('a codec error keeps the preview actions visible instead of stranding the media', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    app.frame({
      type: 'user_input',
      content: [{ type: 'file', file: { path: FILE_VIDEO, name: 'clip.mp4', mime: 'video/mp4' } }],
    })
    await settle()
    app.answerPrepare(app.prepares().at(-1), { resourceId: 'host-codec' })
    await settle()
    const video = app.document.querySelector('video')
    assert.ok(video)
    const actions = video.parentElement
    assert.equal(actions.querySelectorAll('button').length, 2)
    // Honest simulation: JSDOM cannot decode media, so only the error event a real
    // <video> would raise is dispatched — no playback is pretended.
    video.dispatchEvent(new app.window.Event('error'))
    await settle()
    assert.match(actions.textContent, /could not be played/i, 'the codec failure is reported honestly')
    const buttons = [...actions.querySelectorAll('button')].map((button) => button.textContent.trim())
    assert.deepEqual(buttons, ['Open', 'Save'], 'open/save stay available behind a broken inline preview')
    assert.ok(actions.querySelector('a[download]'), 'the download fallback stays available')
  } finally {
    app.close()
  }
})

test('a generated image and its Markdown copy share one Host read and release the Host lease exactly once', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    app.frame({ type: 'image', url: ARTIFACT })
    app.frame({ type: 'text', content: `![from markdown](${ARTIFACT})` })
    await settle()
    assert.equal(app.prepares().length, 1, 'the image part and the Markdown copy share one authenticated read')
    assert.equal(app.prepares()[0].path, ARTIFACT)
    const shared = app.answerPrepare(app.prepares()[0], { resourceId: 'host-shared' })
    await settle()
    assert.equal(
      app.document.querySelectorAll('img[src="' + shared.uri + '"]').length,
      2,
      'both consumers display the one spooled resource through distinct lease handles',
    )
    // A configuration reset detaches the conversation, unmounting BOTH consumers;
    // the single shared Host lease must be released exactly once, not per consumer.
    app.receive({ type: 'configuration.changed' })
    await settle()
    assert.equal(
      app.sent('media.release').filter((message) => message.resourceId === 'host-shared').length,
      1,
      'one shared Host lease, one release',
    )
  } finally {
    app.close()
  }
})

test('a new ready epoch reopens the socket, resyncs history, and re-prepares an unchanged path while discarding the superseded read', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    // The image lives in persisted history, not merely in unsaved webview state.
    app.frame({ type: 'image', url: ARTIFACT })
    await settle()
    const first = app.prepares().at(-1)
    assert.equal(first.path, ARTIFACT)
    const socketsBefore = app.sent('ws.open').length
    const pagesBefore = app.sent('http.historyPage').length
    // Refresh starts a new ready epoch: the Host closes the previous socket, the
    // webview opens a FRESH one, resyncs history, and re-prepares the same path.
    app.document.querySelector('button[aria-label="Refresh Sessions"]').click()
    await settle()
    assert.equal(socketsBefore, app.sent('ws.open').length, 'no socket is opened before the new epoch is admitted')
    await app.answerReady(app.sent('ready').at(-1))
    assert.ok(app.sent('ws.open').length > socketsBefore, 'the new epoch opened a fresh socket')
    // Sends are accepted only on the fresh socket; the fixture refuses a stale id.
    const freshSocket = app.sent('ws.open').at(-1).socketId
    assert.notEqual(freshSocket, app.sent('ws.open')[socketsBefore - 1]?.socketId, 'the new epoch never reuses the previous socket id')
    assert.ok(app.sent('http.historyPage').length > pagesBefore, 'the new epoch resynced history')
    assert.equal(
      app.sent('media.cancel').filter((message) => message.prepareRequestId === first.requestId).length,
      1,
      'the superseded in-flight read is aborted by requestId',
    )
    const second = app.prepares().at(-1)
    assert.notEqual(second.requestId, first.requestId, 'the unchanged path re-prepared against the new fence')
    // The old read settles late: its spooled resource is discarded, never shown.
    app.answerPrepare(first, { resourceId: 'host-stale' })
    await settle()
    assert.equal(app.document.querySelector('img[src^="vscode-webview://spool/"]'), null, 'a read superseded by the new epoch never lands')
    assert.equal(
      app.sent('media.cancel').some((message) => message.resourceId === 'host-stale'),
      true,
      'the abandoned spooled resource is discarded',
    )
    app.answerPrepare(second, { resourceId: 'host-fresh' })
    await settle()
    assert.ok(app.document.querySelector('img[src="vscode-webview://spool/host-fresh.png"]'), 'the fresh read lands')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})

test('streaming frames keep one Host read for an unchanged Markdown image, release only on unmount, and the pending read survives every frame', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    // A streamed assistant turn whose text is re-rendered frame by frame. Each
    // ``v-html`` pass recreates the subtree, so the same raw image reference shows
    // up on a brand-new node on every frame — the case that must NOT refetch per
    // frame.
    const render = (n) => `![pic](${ARTIFACT})\n\nstreamed line ${n}`
    app.frame({ type: 'text', content: render(1) })
    await settle()
    app.frame({ type: 'text', content: render(2) })
    app.frame({ type: 'text', content: render(3) })
    app.frame({ type: 'text', content: render(4) })
    await settle()
    assert.equal(app.prepares().length, 1, 'the unchanged Markdown image is prepared exactly once across frames')
    assert.equal(app.sent('media.release').length, 0, 'no Host lease is released while the image is still displayed')
    // The read stays pending through every frame and still lands on the live node.
    const prepared = app.answerPrepare(app.prepares()[0], { resourceId: 'host-stream' })
    await settle()
    assert.ok(
      app.document.querySelectorAll(`img[src="${prepared.uri}"]`).length >= 1,
      'the read that outlived several frames lands on the current Markdown node',
    )
    // Detaching the conversation unmounts the renderer; the single shared lease is
    // released exactly once, never per frame.
    app.receive({ type: 'configuration.changed' })
    await settle()
    assert.equal(
      app.sent('media.release').filter((message) => message.resourceId === 'host-stream').length,
      1,
      'the shared lease is released exactly once on unmount',
    )
  } finally {
    app.close()
  }
})

test('a failed Markdown artifact surfaces ONE visible error status with no per-frame retry, and advertises no unavailable open/save', async () => {
  const app = await boot()
  try {
    app.holdPrepare(true)
    // The same unchanged Markdown image streams across several frames, and every
    // read of it fails the way the real Host reports a bad artifact route.
    const render = (n) => `![broken](${ARTIFACT})\n\nstreamed line ${n}`
    app.frame({ type: 'text', content: render(1) })
    await settle()
    assert.equal(app.prepares().length, 1, 'the failing Markdown image is prepared exactly once')
    app.failPrepare(app.prepares()[0], 'artifact read failed (HTTP 500)')
    await settle()

    const content = () => app.document.querySelector('.md-content')
    const status = content().querySelector('.kt-media-status.is-error')
    assert.ok(status, 'the failed Markdown image surfaces a visible error status instead of a blank node')
    assert.equal(status.getAttribute('role'), 'status', 'the status is announced, not an invisible title tooltip')
    assert.ok(status.textContent.trim().length > 0, 'the status carries visible, localized text')
    // Errors stay SAFE: neither the raw backend message nor a token reaches the DOM.
    assert.doesNotMatch(status.textContent, /HTTP 500|artifact read failed|host-secret/i)
    // The webview owns no per-image handler here, so it must NOT advertise a fake
    // retry/open/save for a resource that was never prepared.
    assert.equal(status.querySelector('button'), null, 'no open/save/retry control is offered for the unavailable resource')

    // The image keeps streaming on fresh nodes; the kept failure replays the
    // status without firing a fresh Host read per frame (no busy retry loop).
    app.frame({ type: 'text', content: render(2) })
    app.frame({ type: 'text', content: render(3) })
    app.frame({ type: 'text', content: render(4) })
    await settle()
    assert.equal(app.prepares().length, 1, 'the failing source is never re-prepared per stream frame')
    assert.ok(content().querySelector('.kt-media-status.is-error'), 'the visible error status persists across frames')
    assert.deepEqual(app.errors, [], 'built webview emitted no runtime errors')
  } finally {
    app.close()
  }
})
