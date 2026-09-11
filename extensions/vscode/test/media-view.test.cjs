const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { createMediaView } = require('../src/host/mediaView.cjs')
const { renderWebviewHtml } = require('../src/host/webview.cjs')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'
const tick = () => new Promise((resolve) => setImmediate(resolve))
const prepare = (requestId = 1, overrides = {}) => ({
  type: 'media.prepare',
  requestId,
  path: REF,
  readyId: 7,
  selectionVersion: 0,
  ...overrides,
})

function runtime() {
  return {
    state: { selection: { session: 's', creature: 'c' } },
    runtimeEpoch: 7,
    pendingSelectionMutations: 0,
    ownsArtifactRead: () => true,
  }
}

function mediaBody(text) {
  let done = false
  return {
    getReader: () => ({
      async read() {
        if (done) return { done: true, value: undefined }
        done = true
        return { done: false, value: Buffer.from(text) }
      },
      async cancel() {},
    }),
  }
}

function fakeEnv({ saveTarget = null, saveTargetSequence = null } = {}) {
  const commands = []
  const tabs = []
  const groups = []
  let disposed = false
  let saveCalls = 0
  const vscode = {
    Uri: { file: (value) => ({ fsPath: String(value), toString: () => `file://${value}` }) },
    commands: { executeCommand: async (...args) => commands.push(args) },
    window: {
      showSaveDialog: async () => {
        if (saveTargetSequence) return saveTargetSequence[saveCalls++]
        return saveTarget
      },
      tabGroups: {
        all: groups,
        onDidChangeTabs: (callback) => {
          tabs.push(callback)
          return {
            dispose: () => {
              disposed = true
            },
          }
        },
      },
    },
  }
  const webview = {
    cspSource: 'vscode-webview://origin',
    asWebviewUri: (uri) => `vscode-webview://origin/spool/${path.basename(uri.fsPath)}`,
  }
  return { vscode, webview, commands, tabs, groups, isDisposed: () => disposed }
}

// Deterministic stand-in for the interval surface so the sweep can be driven by
// hand instead of waiting on a wall clock.
function fakeTimers() {
  let callback = null
  let cleared = 0
  return {
    timers: {
      setInterval: (fn) => {
        callback = fn
        return { unref() {} }
      },
      clearInterval: () => {
        cleared++
        callback = null
      },
    },
    tick: () => callback?.(),
    active: () => callback !== null,
    clearedCount: () => cleared,
  }
}

function mediaResponse(text) {
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? 'video/mp4' : null) },
    body: mediaBody(text),
  }
}

test('createMediaView scopes a private root, wires the localResourceRoot, and needs a storage dir', async () => {
  const { vscode, webview } = fakeEnv()
  assert.equal(createMediaView({ vscode, webview, storageDir: null }), null)

  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode, webview, storageDir })
  try {
    assert.equal(media.resourceRoot.fsPath, storageDir)
    const root = await media.start()
    assert.equal(path.dirname(root), storageDir)
    assert.match(path.basename(root), /^kt-media-/)
  } finally {
    await media.dispose()
  }
})

test('open drives a non-text tab and a closed tab reconciles the editor lease', async () => {
  const env = fakeEnv()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('video-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await media.mediaHost.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(env.commands.length, 1)
    assert.equal(env.commands[0][0], 'vscode.open')
    const openedPath = env.commands[0][1].fsPath
    assert.match(openedPath, /clip\.mp4$/)

    // None of the media bytes ever reach the webview as a file path; only the URI did.
    media.mediaHost.releaseOwned({ resourceId: resource.resourceId, lease: 'webview' })
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), true)
    // The tab close arrives as a tab.input.uri (not a TextDocument), reconciling the lease.
    env.tabs[0]({ closed: [{ input: { uri: { fsPath: openedPath } } }] })
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), false)
  } finally {
    await media.dispose()
  }
})

test('releaseView then a later tab close deletes the resource and its spool entry', async () => {
  const env = fakeEnv()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('video-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await media.mediaHost.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    const openedPath = env.commands[0][1].fsPath

    // The view is disposed while the editor tab is still open: the webview lease
    // drops, but the editor lease and its path mapping must be retained.
    media.releaseView()
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), true)
    assert.deepEqual(media.mediaHost.editorFilePaths(), [openedPath])

    // The close that lands after the view is gone still reclaims the file.
    env.tabs[0]({ closed: [{ input: { uri: { fsPath: openedPath } } }] })
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), false)
    assert.deepEqual(media.mediaHost.editorFilePaths(), [])
    assert.deepEqual(await media.mediaHost.spool.ownerRootEntries(), [])
  } finally {
    await media.dispose()
  }
})

test('the reconciliation sweep stays idle without editor leases and cleans up on dispose', async () => {
  const env = fakeEnv()
  const fake = fakeTimers()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir, timers: fake.timers })
  try {
    await media.start()
    // No editor lease has been taken, so no timer is armed.
    assert.equal(fake.active(), false)

    media.mediaHost.spool.fetchImpl = async () => mediaResponse('video-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await media.mediaHost.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(fake.active(), true, 'the first editor lease arms the sweep')

    // A retained editor lease after view dispose keeps the sweep armed.
    media.releaseView()
    assert.equal(fake.active(), true)
    await media.dispose()
    assert.equal(fake.active(), false)
    assert.ok(fake.clearedCount() >= 1, 'dispose clears the retained sweep')
  } finally {
    await media.dispose()
  }
})

test('a periodic sweep reclaims an editor lease whose close event was missed', async () => {
  const env = fakeEnv()
  const fake = fakeTimers()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir, timers: fake.timers })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('video-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await media.mediaHost.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    const openedPath = env.commands[0][1].fsPath

    // The tab is gone from tabGroups.all and no close event was ever delivered, so
    // only the sweep can notice the stale editor lease.
    media.releaseView()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), true)
    assert.equal(env.groups.length, 0)
    fake.tick()
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), false)
    // With no editor lease left the sweep disarms itself.
    assert.equal(fake.active(), false)
    assert.deepEqual(await media.mediaHost.spool.ownerRootEntries(), [])
    assert.equal(openedPath, env.commands[0][1].fsPath)
  } finally {
    await media.dispose()
  }
})

test('the sweep keeps a lease whose editor tab is still listed in tabGroups.all', async () => {
  const env = fakeEnv()
  const fake = fakeTimers()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir, timers: fake.timers })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('video-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await media.mediaHost.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    const openedPath = env.commands[0][1].fsPath
    media.releaseView()
    env.groups.push({ tabs: [{ input: { uri: { fsPath: openedPath } } }] })
    fake.tick()
    await tick()
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), true, 'a live tab keeps the lease')
  } finally {
    await media.dispose()
  }
})

test('setBackend aborts an in-flight prepare when the credentials change on a reused runtime', async () => {
  const env = fakeEnv()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  let aborted = false
  let markStarted
  const started = new Promise((resolve) => {
    markStarted = resolve
  })
  try {
    await media.start()
    media.setBackend('http://127.0.0.1:8000', 'token-a')
    media.mediaHost.spool.fetchImpl = async (url, options) => ({
      ok: true,
      status: 200,
      headers: { get: () => 'video/mp4' },
      body: {
        getReader: () => ({
          async read() {
            markStarted()
            await gate
            aborted = options.signal.aborted
            if (aborted) throw Error('aborted')
            return { done: true, value: undefined }
          },
          async cancel() {},
        }),
      },
    })
    const attempt = media.mediaHost.handle(prepare(1), runtime())
    await started
    // The runtime is reused but the resolved credentials changed: the stale fetch must abort.
    media.setBackend('http://127.0.0.1:8000', 'token-b')
    release()
    await assert.rejects(attempt, /cancelled|failed/)
    assert.equal(aborted, true)
    assert.equal(media.mediaHost.spool.token, 'token-b')
  } finally {
    await media.dispose()
  }
})

test('setBackend does not abort an in-flight prepare when the backend and token are unchanged', async () => {
  const env = fakeEnv()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  let aborted = false
  let first = true
  let markStarted
  const started = new Promise((resolve) => {
    markStarted = resolve
  })
  try {
    await media.start()
    media.setBackend('http://127.0.0.1:8000', 'token-a')
    media.mediaHost.spool.fetchImpl = async (url, options) => ({
      ok: true,
      status: 200,
      headers: { get: () => 'video/mp4' },
      body: {
        getReader: () => ({
          async read() {
            if (!first) return { done: true, value: undefined }
            first = false
            markStarted()
            await gate
            aborted = options.signal.aborted
            if (aborted) throw Error('aborted')
            return { done: false, value: Buffer.from('video-bytes') }
          },
          async cancel() {},
        }),
      },
    })
    const attempt = media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    await started
    // Re-resolving the identical backend + token must not disturb the in-flight read.
    media.setBackend('http://127.0.0.1:8000', 'token-a')
    release()
    const resource = await attempt
    assert.equal(aborted, false)
    assert.equal(resource.name, 'clip.mp4')
  } finally {
    await media.dispose()
  }
})

test('save copies the spooled file through the Save dialog and reports a completed save', async () => {
  const target = path.join(await fs.mkdtemp(path.join(os.tmpdir(), 'kt-save-')), 'clip.mp4')
  const env = fakeEnv({ saveTarget: { fsPath: target } })
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('saved-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    const result = await media.mediaHost.handle({ type: 'media.save', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(await fs.readFile(target, 'utf8'), 'saved-bytes')
    assert.equal(result.ok, true)
    assert.equal(result.cancelled, false)
  } finally {
    await media.dispose()
  }
})

test('a dismissed Save dialog returns a distinguishable cancelled result and copies nothing', async () => {
  const env = fakeEnv({ saveTarget: null })
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  try {
    await media.start()
    media.mediaHost.spool.fetchImpl = async () => mediaResponse('saved-bytes')
    const resource = await media.mediaHost.handle(prepare(1, { name: 'clip.mp4' }), runtime())
    const result = await media.mediaHost.handle({ type: 'media.save', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(result.ok, false)
    assert.equal(result.cancelled, true)
    // The spooled resource is still live, so a later save attempt can succeed.
    assert.equal(await media.mediaHost.spool.isResourceLive(resource.resourceId), true)
  } finally {
    await media.dispose()
  }
})

test('dispose reclaims the whole root and tears down the tab listener', async () => {
  const env = fakeEnv()
  const storageDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediaview-'))
  const media = createMediaView({ vscode: env.vscode, webview: env.webview, storageDir })
  const root = await media.start()
  assert.equal((await fs.readdir(storageDir)).includes(path.basename(root)), true)
  await media.dispose()
  assert.equal((await fs.readdir(storageDir)).includes(path.basename(root)), false)
  assert.equal(env.isDisposed(), true)
})

test('webview CSP grants media-src only when the Host wires a media source', () => {
  const base = renderWebviewHtml({ cspSource: 'vscode-webview://origin', scriptUri: 'x', styleUri: 'y', nonce: 'n' })
  assert.doesNotMatch(base, /media-src/)
  const wired = renderWebviewHtml({
    cspSource: 'vscode-webview://origin',
    scriptUri: 'x',
    styleUri: 'y',
    nonce: 'n',
    mediaSrc: 'vscode-webview://origin',
  })
  assert.match(wired, /media-src vscode-webview:\/\/origin/)
  assert.doesNotMatch(wired, /media-src[^;]*https?:/)
})
