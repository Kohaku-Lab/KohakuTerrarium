const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { MediaHost, MAX_CANCELLED_REQUESTS } = require('../src/host/mediaHost.cjs')
const { MediaSpool } = require('../src/host/mediaSpool.cjs')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'
const TOKEN = 'host-secret-token'
const tick = () => new Promise((resolve) => setImmediate(resolve))
const waitFor = async (predicate, attempts = 800) => {
  for (let i = 0; i < attempts; i++) {
    if (predicate()) return true
    await tick()
  }
  return predicate()
}

function bodyOf(bytes) {
  let done = false
  return {
    getReader() {
      return {
        async read() {
          if (done) return { done: true, value: undefined }
          done = true
          return { done: false, value: Buffer.from(bytes) }
        },
        async cancel() {},
      }
    },
  }
}

function pngResponse(bytes = 'media-bytes') {
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? 'video/mp4' : null) },
    body: bodyOf(bytes),
  }
}

async function build({ openEditor = null, saveAs = null, fetchImpl = null, limits = {} } = {}) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediahost-'))
  const spool = new MediaSpool({
    base: 'http://127.0.0.1:8000',
    token: TOKEN,
    spoolBase: base,
    fetchImpl: fetchImpl || (async () => pngResponse()),
    asWebviewUri: (filePath) => `vscode-webview://spool/${path.basename(filePath)}`,
    limits,
    pid: process.pid,
    instanceId: 'mediahost-test',
  })
  const host = new MediaHost({ spool, openEditor, saveAs })
  await host.start()
  return { host, spool, base }
}

// A runtime stand-in exposing only the fence surface MediaHost consults.
function runtime({ selection = { session: 's', creature: 'c' }, readyId = 7, pending = 0, owns = true } = {}) {
  return {
    state: { selection },
    runtimeEpoch: readyId,
    pendingSelectionMutations: pending,
    ownsArtifactRead: () => owns,
  }
}

const PREPARE = (overrides = {}) => ({ type: 'media.prepare', requestId: 1, path: REF, readyId: 7, selectionVersion: 0, ...overrides })

test('prepare streams to the spool and returns only the post-spool fence fields', async () => {
  const { host } = await build()
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    assert.equal(resource.state, 'exposed')
    assert.match(resource.sha256, /^[0-9a-f]{64}$/)
    assert.equal(resource.mime, 'video/mp4')
    assert.equal(resource.name, 'clip.mp4')
    assert.equal(resource.uri.includes(TOKEN), false)
    assert.equal(Object.hasOwn(resource, 'filePath'), false)
  } finally {
    await host.dispose()
  }
})

test('prepare is fenced by selection, ready epoch, pending intent, and a canonical route', async () => {
  const { host } = await build()
  try {
    await assert.rejects(host.handle(PREPARE()), /Unknown media reference|Select a Creature/)
    await assert.rejects(host.handle(PREPARE(), runtime({ selection: null })), /Select a Creature/)
    await assert.rejects(host.handle(PREPARE(), runtime({ owns: false })), /ownership changed/)
    await assert.rejects(host.handle(PREPARE(), runtime({ pending: 1 })), /ownership changed/)
    await assert.rejects(
      host.handle(PREPARE({ path: '/api/sessions/graph_1/artifacts/../secret.mp4' }), runtime()),
      /Unknown media reference/,
    )
    await assert.rejects(
      host.handle(PREPARE({ path: 'https://evil.example/api/sessions/graph_1/artifacts/x.mp4' }), runtime()),
      /Unknown media reference/,
    )
  } finally {
    await host.dispose()
  }
})

test('release deletes only after the last lease; open/save drive injected adapters', async () => {
  const opened = []
  const saved = []
  const { host, spool } = await build({
    openEditor: async (resource) => opened.push(resource),
    saveAs: async (resource) => {
      saved.push(resource)
      return { saved: true }
    },
  })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    // Opening an editor adds an editor lease before the panel releases its own.
    await host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(opened.length, 1)
    assert.equal(opened[0].name, 'clip.mp4')
    assert.equal(
      await host
        .handle({ type: 'media.release', requestId: 3, resourceId: resource.resourceId, lease: 'webview' }, runtime())
        .then((r) => r.ok),
      true,
    )
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), true, 'editor lease keeps the file alive')
    // A closed editor tab reconciles the editor lease by path.
    host.closeEditor(opened[0].filePath)
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), false)
    // save reads the still-live spooled file and preserves its name.
    const again = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    await host.handle({ type: 'media.save', requestId: 5, resourceId: again.resourceId }, runtime())
    assert.equal(saved.length, 1)
    assert.match(saved[0].filePath, /clip\.mp4$/)
  } finally {
    await host.dispose()
  }
})

test('re-opening the same resource does not stack editor leases', async () => {
  const opened = []
  const { host, spool } = await build({ openEditor: async (resource) => opened.push(resource) })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    await host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    await host.handle({ type: 'media.open', requestId: 3, resourceId: resource.resourceId }, runtime())
    assert.equal(opened.length, 2, 'the tab adapter is still driven per request')
    await host.handle({ type: 'media.release', requestId: 4, resourceId: resource.resourceId, lease: 'webview' }, runtime())
    await tick()
    // One editor lease, not two: closing the single tab reclaims the file.
    assert.equal(host.closeEditor(opened[0].filePath), true)
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), false)
  } finally {
    await host.dispose()
  }
})

test('open rolls back the editor lease when the adapter fails', async () => {
  const { host, spool } = await build({
    openEditor: async () => {
      throw Error('tab refused')
    },
  })
  try {
    const resource = await host.handle(PREPARE(), runtime())
    await assert.rejects(host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime()), /tab refused/)
    // The rolled-back editor lease leaves just the webview lease, so the file survives release of neither.
    assert.equal(await spool.isResourceLive(resource.resourceId), true)
  } finally {
    await host.dispose()
  }
})

test('cancel discards a resource regardless of outstanding leases', async () => {
  const { host, spool } = await build()
  try {
    const resource = await host.handle(PREPARE(), runtime())
    await host.handle({ type: 'media.cancel', requestId: 2, resourceId: resource.resourceId }, runtime())
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), false)
  } finally {
    await host.dispose()
  }
})

test('cancel by requestId aborts an in-flight prepare before a resourceId exists', async () => {
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  let aborted = false
  let markStarted
  const readStarted = new Promise((resolve) => {
    markStarted = resolve
  })
  const fetchImpl = async (url, options) => ({
    ok: true,
    status: 200,
    headers: { get: () => 'video/mp4' },
    body: {
      getReader() {
        return {
          async read() {
            markStarted()
            await gate
            if (options.signal.aborted) {
              aborted = true
              throw Error('aborted')
            }
            return { done: true, value: undefined }
          },
          async cancel() {},
        }
      },
    },
  })
  const { host, spool } = await build({ fetchImpl })
  try {
    const attempt = host.handle(PREPARE({ requestId: 11 }), runtime())
    // Wait until the body read is attached, then cancel while it is in flight.
    await readStarted
    await host.handle({ type: 'media.cancel', requestId: 12, prepareRequestId: 11 }, runtime())
    release()
    await assert.rejects(attempt, /cancelled|failed/)
    assert.equal(aborted, true)
    await tick()
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await host.dispose()
  }
})

test('a cancel that lands before its prepare is honored once and never blocks a distinct request', async () => {
  const { host } = await build()
  try {
    await host.handle({ type: 'media.cancel', requestId: 1, prepareRequestId: 42 }, runtime())
    assert.equal(host.cancelledRequests.has(42), true)
    await assert.rejects(host.handle(PREPARE({ requestId: 42 }), runtime()), /cancelled/)
    // The record is consumed exactly once; a distinct requestId is served normally.
    assert.equal(host.cancelledRequests.has(42), false)
    const resource = await host.handle(PREPARE({ requestId: 43, name: 'clip.mp4' }), runtime())
    assert.equal(resource.state, 'exposed')
  } finally {
    await host.dispose()
  }
})

test('the cancel-before-prepare record stays bounded and never stalls a distinct request', async () => {
  const { host } = await build()
  try {
    for (let index = 0; index < MAX_CANCELLED_REQUESTS + 64; index++)
      await host.handle({ type: 'media.cancel', requestId: index, prepareRequestId: 1_000_000 + index }, runtime())
    assert.ok(host.cancelledRequests.size <= MAX_CANCELLED_REQUESTS, 'cancel-before-prepare records are bounded')
    // The oldest stale records were evicted; a fresh requestId is still served.
    const resource = await host.handle(PREPARE({ requestId: 9_000_000, name: 'clip.mp4' }), runtime())
    assert.equal(resource.state, 'exposed')
  } finally {
    await host.dispose()
  }
})

test('save reports a distinguishable cancelled result when the dialog is dismissed', async () => {
  const { host, spool } = await build({ saveAs: async () => ({ saved: false }) })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    const result = await host.handle({ type: 'media.save', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(result.ok, false)
    assert.equal(result.cancelled, true)
    // A declined save leaves the spooled resource untouched.
    assert.equal(await spool.isResourceLive(resource.resourceId), true)
  } finally {
    await host.dispose()
  }
})

test('save reports ok with cancelled false when the adapter writes the file', async () => {
  const written = []
  const { host } = await build({
    saveAs: async (resource) => {
      written.push(resource)
      return { saved: true }
    },
  })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    const result = await host.handle({ type: 'media.save', requestId: 2, resourceId: resource.resourceId }, runtime())
    assert.equal(result.ok, true)
    assert.equal(result.cancelled, false)
    assert.equal(written.length, 1)
  } finally {
    await host.dispose()
  }
})

test('abortAll aborts an in-flight prepare so no transport is orphaned', async () => {
  let started = false
  let aborted = false
  let markStarted
  const readStarted = new Promise((resolve) => {
    markStarted = resolve
  })
  const fetchImpl = async (url, options) => {
    started = true
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'video/mp4' },
      body: {
        getReader() {
          return {
            async read() {
              markStarted()
              await new Promise((resolve) => options.signal.addEventListener('abort', resolve, { once: true }))
              aborted = true
              throw Error('aborted')
            },
            async cancel() {},
          }
        },
      },
    }
  }
  const { host } = await build({ fetchImpl })
  try {
    const attempt = host.handle(PREPARE(), runtime())
    await readStarted
    assert.equal(started, true)
    host.abortAll()
    await assert.rejects(attempt, /cancelled|failed/)
    assert.equal(aborted, true)
  } finally {
    await host.dispose()
  }
})

test('abortAll drops a queued prepare before its fetch ever starts', async () => {
  const started = []
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  const fetchImpl = async (url) => {
    const index = started.push(url)
    let done = false
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'video/mp4' },
      body: {
        getReader: () => ({
          async read() {
            if (done) return { done: true, value: undefined }
            done = true
            if (index === 1) await gate
            return { done: false, value: Buffer.from('x') }
          },
          async cancel() {},
        }),
      },
    }
  }
  const { host, spool } = await build({ limits: { maxConcurrent: 1 }, fetchImpl })
  try {
    const first = host.handle(PREPARE({ requestId: 1, name: '1.mp4' }), runtime())
    await waitFor(() => started.length === 1)
    // The second prepare is queued behind the single active buffer, never fetched.
    const queued = host.handle(PREPARE({ requestId: 2, name: '2.mp4' }), runtime())
    // The active fetch has opened its spool file; the queued one has fetched nothing.
    assert.equal((await spool.ownerRootEntries()).length, 1)
    host.abortAll()
    // Attach the settlement handlers before the aborts can surface as rejections.
    const firstRejects = assert.rejects(first, /cancelled|failed/)
    const queuedRejects = assert.rejects(queued, /cancelled|failed/)
    release()
    await firstRejects
    await queuedRejects
    await tick()
    assert.equal(started.length, 1, 'the queued prepare never reached the network')
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await host.dispose()
  }
})

test('releaseView drops webview leases but keeps a resource an editor tab still holds', async () => {
  const { host, spool } = await build({ openEditor: async () => {} })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    await host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    host.releaseView()
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), true, 'the editor lease survives view dispose')
  } finally {
    await host.dispose()
  }
})

test('releaseView keeps the path mapping so a later tab close reclaims the spooled file', async () => {
  const { host, spool } = await build({ openEditor: async () => {} })
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    await host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    const filePath = host.editorFilePaths()[0]
    assert.equal(typeof filePath, 'string')
    // The view is gone; the editor lease and its path mapping must survive so the
    // close that lands afterwards still finds the resource.
    host.releaseView()
    await tick()
    assert.deepEqual(host.editorFilePaths(), [filePath])
    assert.equal(host.closeEditor(filePath), true)
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), false)
    // Nothing is left under the per-view root once the last lease is gone.
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await host.dispose()
  }
})

test('onEditorLeaseChange reports the editor lease count on open and close', async () => {
  const counts = []
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-mediahost-'))
  const spool = new MediaSpool({
    base: 'http://127.0.0.1:8000',
    token: TOKEN,
    spoolBase: base,
    fetchImpl: async () => pngResponse(),
    instanceId: 'mediahost-lease-count',
  })
  const host = new MediaHost({ spool, openEditor: async () => {}, onEditorLeaseChange: (count) => counts.push(count) })
  await host.start()
  try {
    const resource = await host.handle(PREPARE({ name: 'clip.mp4' }), runtime())
    await host.handle({ type: 'media.open', requestId: 2, resourceId: resource.resourceId }, runtime())
    host.closeEditor(host.editorFilePaths()[0])
    assert.deepEqual(counts, [1, 0])
  } finally {
    await host.dispose()
  }
})

test('a disposed host cleans its root, clears cancel records, and rejects later requests', async () => {
  const { host, spool } = await build()
  const root = spool.root
  await host.handle({ type: 'media.cancel', requestId: 1, prepareRequestId: 5 }, runtime())
  assert.equal(host.cancelledRequests.size, 1)
  await host.dispose()
  assert.equal(host.cancelledRequests.size, 0)
  assert.equal((await fs.readdir(path.dirname(root))).includes(path.basename(root)), false)
  await assert.rejects(host.handle(PREPARE(), runtime()), /disposed/)
})
