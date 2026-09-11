const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { MediaSpool, MediaSpoolError, OWNER_SCHEMA } = require('../src/host/mediaSpool.cjs')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'
const REF_DIR = '/api/sessions/graph_1/artifacts/generated_videos'
const TOKEN = 'host-secret-token'
const tick = () => new Promise((resolve) => setImmediate(resolve))
// Poll until a predicate holds; bounded so a regression fails instead of hanging.
const waitFor = async (predicate, attempts = 800) => {
  for (let i = 0; i < attempts; i++) {
    if (predicate()) return true
    await tick()
  }
  return predicate()
}
const lastSegment = (url) => url.slice(url.lastIndexOf('/') + 1)

async function tmpBase() {
  return fs.mkdtemp(path.join(os.tmpdir(), 'kt-spool-test-'))
}

// A byte body that streams without a known length, recording read/write order
// so a test can prove the spool applies backpressure instead of draining.
function trackedBody(chunks, events, { afterChunk = null } = {}) {
  let index = 0
  return {
    getReader() {
      return {
        async read() {
          events.push('read')
          if (afterChunk && index === 1) await afterChunk()
          if (index >= chunks.length) return { done: true, value: undefined }
          return { done: false, value: chunks[index++] }
        },
        async cancel() {},
      }
    },
  }
}

function bytesBody(bytes) {
  return trackedBody([bytes], [])
}

function response({ body, contentType = 'video/mp4', contentLength = null, ok = true, status = 200 }) {
  return {
    ok,
    status,
    headers: {
      get: (name) => (name.toLowerCase() === 'content-type' ? contentType : name.toLowerCase() === 'content-length' ? contentLength : null),
    },
    body,
  }
}

// fs wrapper that records writes on the returned events array.
function recordingFs(events) {
  return {
    ...fs,
    async open(file, flags) {
      const handle = await fs.open(file, flags)
      return {
        async write(buffer) {
          events?.push('write')
          return handle.write(buffer)
        },
        close: () => handle.close(),
      }
    },
  }
}

async function makeSpool(overrides = {}) {
  const base = overrides.spoolBase || (await tmpBase())
  const spool = new MediaSpool({
    base: 'http://127.0.0.1:8000',
    token: TOKEN,
    spoolBase: base,
    fetchImpl: overrides.fetchImpl || (async () => response({ body: bytesBody(Buffer.from('data')) })),
    asWebviewUri: overrides.asWebviewUri || ((filePath) => `vscode-webview://spool/${path.basename(filePath)}`),
    limits: overrides.limits || {},
    fsImpl: overrides.fsImpl || fs,
    pathImpl: path,
    osImpl: os,
    cryptoImpl: require('node:crypto'),
    pid: overrides.pid ?? process.pid,
    now: overrides.now || (() => Date.now()),
    instanceId: overrides.instanceId || 'instance-a',
    isProcessAlive: overrides.isProcessAlive || (() => false),
  })
  await spool.start()
  return spool
}

async function cleanup(spool) {
  await spool.dispose()
}

test('streams an unknown-length body to disk with backpressure and no default single-file cap', async () => {
  const events = []
  // 12 MiB with NO content-length: no arbitrary single-file ceiling may reject it.
  const chunk = Buffer.alloc(1 << 20, 7)
  const body = trackedBody([chunk, chunk, chunk, chunk, chunk, chunk, chunk, chunk, chunk, chunk, chunk, chunk], events)
  const spool = await makeSpool({ fetchImpl: async (url, options) => response({ body }), fsImpl: recordingFs(events) })
  try {
    const resource = await spool.prepare(REF, { name: 'clip.mp4' })
    assert.equal(resource.bytes, 12 * (1 << 20))
    assert.equal(resource.mime, 'video/mp4')
    assert.match(resource.sha256, /^[0-9a-f]{64}$/)
    assert.equal(resource.state, 'exposed')
    // Backpressure: reads and writes strictly interleave (never drain then write).
    const steps = events.filter((step) => step === 'read' || step === 'write')
    assert.equal(events.filter((step) => step === 'write').length, 12)
    assert.equal(steps[steps.length - 1], 'read')
    for (let index = 1; index < steps.length; index++) {
      assert.notEqual(`${steps[index - 1]},${steps[index]}`, 'read,read', 'a read must wait for the previous write')
    }
  } finally {
    await cleanup(spool)
  }
})

test('the returned webview URI never carries the host token and fetch is redirect-hostile', async () => {
  const calls = []
  const spool = await makeSpool({
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return response({ body: bytesBody(Buffer.from('png')) })
    },
  })
  try {
    const resource = await spool.prepare(REF, { name: 'clip.mp4' })
    assert.equal(calls[0].options.redirect, 'error')
    assert.equal(calls[0].options.headers['X-KT-Host-Token'], TOKEN)
    assert.equal(calls[0].options.headers.authorization, undefined)
    assert.equal(resource.uri.includes(TOKEN), false)
  } finally {
    await cleanup(spool)
  }
})

// A body whose first ``read`` blocks on a per-request gate; ``state`` records
// every fetch entry and the live-buffer high-water mark, so a test can prove the
// active-buffer bound while the FIFO queue drains.
function gatedFetch(state) {
  return async (url) => {
    state.started.push(url)
    state.active++
    state.peak = Math.max(state.peak, state.active)
    let done = false
    let release
    const gate = new Promise((resolve) => {
      release = resolve
    })
    state.gates.push(release)
    return response({
      body: {
        getReader: () => ({
          async read() {
            if (done) {
              state.active--
              return { done: true, value: undefined }
            }
            done = true
            await gate
            return { done: false, value: Buffer.from('x') }
          },
          async cancel() {},
        }),
      },
    })
  }
}

test('requests beyond maxConcurrent queue and all eventually succeed without a refresh', async () => {
  const state = { started: [], gates: [], active: 0, peak: 0 }
  const spool = await makeSpool({ limits: { maxConcurrent: 2 }, fetchImpl: gatedFetch(state) })
  try {
    const names = ['a', 'b', 'c', 'd', 'e']
    const attempts = names.map((name) => spool.prepare(`${REF_DIR}/${name}.mp4`, { name: `${name}.mp4` }))
    assert.equal(await waitFor(() => state.started.length === 2), true, 'only maxConcurrent buffers are active')
    assert.equal(state.started.length, 2)
    for (let index = 0; index < names.length; index++) {
      assert.equal(await waitFor(() => state.gates.length > index), true, `queued request ${index} must reach the network`)
      state.gates[index]()
      if (index < names.length - 1) await waitFor(() => state.started.length > index + 1)
    }
    const resources = await Promise.all(attempts)
    assert.equal(resources.length, 5)
    assert.equal(state.started.length, 5)
    assert.ok(state.peak <= 2, `active buffers never exceed maxConcurrent (peak ${state.peak})`)
    const entries = await spool.ownerRootEntries()
    assert.equal(entries.length, 5)
    for (const name of names)
      assert.ok(
        entries.some((entry) => entry.endsWith(`-${name}.mp4`)),
        `${name} spooled`,
      )
  } finally {
    await cleanup(spool)
  }
})

test('queued requests are granted in FIFO order', async () => {
  const state = { started: [], gates: [], active: 0, peak: 0 }
  const spool = await makeSpool({ limits: { maxConcurrent: 1 }, fetchImpl: gatedFetch(state) })
  try {
    const names = ['x', 'y', 'z']
    const attempts = names.map((name) => spool.prepare(`${REF_DIR}/${name}.mp4`, { name: `${name}.mp4` }))
    await waitFor(() => state.started.length === 1)
    for (let index = 0; index < names.length; index++) {
      assert.equal(await waitFor(() => state.gates.length > index), true)
      state.gates[index]()
      if (index < names.length - 1) await waitFor(() => state.started.length > index + 1)
    }
    await Promise.all(attempts)
    assert.deepEqual(state.started.map(lastSegment), ['x.mp4', 'y.mp4', 'z.mp4'])
    assert.equal(state.peak, 1)
  } finally {
    await cleanup(spool)
  }
})

test('cancelling a queued request drops it before its fetch ever starts', async () => {
  const started = []
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  const spool = await makeSpool({
    limits: { maxConcurrent: 1 },
    fetchImpl: async (url) => {
      const index = started.push(url)
      let done = false
      return response({
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
      })
    },
  })
  try {
    const controller = new AbortController()
    const first = spool.prepare(`${REF_DIR}/1.mp4`, { name: '1.mp4' })
    await waitFor(() => started.length === 1)
    const queued = spool.prepare(`${REF_DIR}/2.mp4`, { name: '2.mp4', signal: controller.signal })
    const third = spool.prepare(`${REF_DIR}/3.mp4`, { name: '3.mp4' })
    assert.deepEqual(started.map(lastSegment), ['1.mp4'], 'only the active request fetched')
    controller.abort()
    await assert.rejects(queued, (error) => error instanceof MediaSpoolError && error.kind === 'cancel')
    release()
    await Promise.all([first, third])
    // The cancelled request never reached the network; the queue skipped it.
    assert.deepEqual(started.map(lastSegment), ['1.mp4', '3.mp4'])
    const entries = await spool.ownerRootEntries()
    assert.equal(entries.length, 2)
    assert.ok(entries.some((entry) => entry.endsWith('-1.mp4')) && entries.some((entry) => entry.endsWith('-3.mp4')))
  } finally {
    await cleanup(spool)
  }
})

test('dispose settles queued requests without ever fetching them', async () => {
  const started = []
  // A stalled header fetch: it never resolves, so only dispose can settle it.
  const spool = await makeSpool({
    limits: { maxConcurrent: 1 },
    fetchImpl: async (url) => {
      started.push(url)
      return new Promise(() => {})
    },
  })
  const active = spool.prepare(`${REF_DIR}/1.mp4`, { name: '1.mp4' })
  await waitFor(() => started.length === 1)
  const queued = spool.prepare(`${REF_DIR}/2.mp4`, { name: '2.mp4' })
  const disposal = spool.dispose()
  // Settle both rejected prepares without an unhandled-rejection window.
  const settled = await Promise.allSettled([active, queued])
  await disposal
  assert.equal(settled[0].status, 'rejected')
  assert.equal(settled[0].reason.kind, 'cancel')
  assert.equal(settled[1].status, 'rejected')
  assert.equal(settled[1].reason.kind, 'disposed')
  assert.deepEqual(started.map(lastSegment), ['1.mp4'], 'a queued request is never fetched')
  await assert.rejects(spool.prepare(REF, { name: 'later.mp4' }), /disposed/)
})

test('cancel mid-stream aborts, deletes the partial file, and leaves no leftover', async () => {
  const controller = new AbortController()
  let reads = 0
  const spool = await makeSpool({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: { get: () => 'video/mp4' },
      body: {
        getReader() {
          return {
            async read() {
              reads++
              if (reads === 1) return { done: false, value: Buffer.from('aaaa') }
              // A mid-stream cancel aborts the spool before the next chunk arrives.
              controller.abort()
              return new Promise(() => {})
            },
            async cancel() {},
          }
        },
      },
    }),
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4', signal: controller.signal }), (error) => {
      assert.ok(error instanceof MediaSpoolError)
      assert.equal(error.kind, 'cancel')
      return true
    })
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})

test('idle timeout fires when the stream stalls, with no leftover file', async () => {
  let stalled = false
  const spool = await makeSpool({
    limits: { idleTimeoutMs: 30 },
    fetchImpl: async () =>
      response({
        body: {
          getReader() {
            return {
              async read() {
                if (!stalled) {
                  stalled = true
                  return { done: false, value: Buffer.from('first') }
                }
                return new Promise(() => {})
              },
              async cancel() {},
            }
          },
        },
      }),
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4' }), (error) => {
      assert.equal(error.kind, 'timeout')
      return true
    })
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})

test('an explicit bound is a hard oversize error with no fallback and no leftover', async () => {
  const chunk = Buffer.alloc(1024)
  const spool = await makeSpool({
    limits: { maxBytes: 4096 },
    fetchImpl: async () =>
      response({
        body: trackedBody(
          Array.from({ length: 8 }, () => chunk),
          [],
        ),
      }),
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'big.mp4' }), (error) => {
      assert.equal(error.kind, 'oversize')
      return true
    })
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})

test('disk write failures surface honestly as a disk error and remove the partial file', async () => {
  const failingFs = {
    ...fs,
    async open(file, flags) {
      const handle = await fs.open(file, flags)
      return {
        async write() {
          const error = new Error('no space left on device')
          error.code = 'ENOSPC'
          throw error
        },
        close: () => handle.close(),
      }
    },
  }
  const spool = await makeSpool({
    fsImpl: failingFs,
    fetchImpl: async () => response({ body: bytesBody(Buffer.from('payload')) }),
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4' }), (error) => {
      assert.ok(error instanceof MediaSpoolError)
      assert.equal(error.kind, 'disk')
      assert.equal(error.code, 'ENOSPC')
      return true
    })
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})

test('a spooled resource is deleted only after every lease is released', async () => {
  const spool = await makeSpool({ fetchImpl: async () => response({ body: bytesBody(Buffer.from('media')) }) })
  try {
    const resource = await spool.prepare(REF, { name: 'clip.mp4' })
    assert.equal(await spool.isResourceLive(resource.resourceId), true)
    spool.acquire(resource.resourceId, 'editor')
    spool.release(resource.resourceId, 'webview')
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), true, 'editor lease keeps the file alive')
    spool.release(resource.resourceId, 'editor')
    await tick()
    assert.equal(await spool.isResourceLive(resource.resourceId), false)
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})

test('start writes an owner marker and sweeps only dead-pid roots of our schema', async () => {
  const base = await tmpBase()
  const makeRoot = async (name, marker) => {
    const root = path.join(base, name)
    await fs.mkdir(root, { recursive: true })
    if (marker !== undefined)
      await fs.writeFile(path.join(root, 'owner.json'), typeof marker === 'string' ? marker : JSON.stringify(marker))
  }
  await makeRoot('kt-media-dead', { schema: OWNER_SCHEMA, instanceId: 'old', pid: 111, startedAt: 1 })
  await makeRoot('kt-media-live', { schema: OWNER_SCHEMA, instanceId: 'live', pid: 222, startedAt: 1 })
  await makeRoot('kt-media-foreign', { schema: 'someone-else/v1', instanceId: 'x', pid: 111, startedAt: 1 })
  await makeRoot('kt-media-malformed', '{ not json')
  await makeRoot('kt-media-unowned', undefined)
  await fs.mkdir(path.join(base, 'unrelated-dir'), { recursive: true })

  const spool = await makeSpool({
    spoolBase: base,
    instanceId: 'self-instance',
    pid: 999,
    isProcessAlive: (pid) => pid === 222,
  })
  try {
    const exists = async (name) => (await fs.readdir(base)).includes(name)
    assert.equal(await exists('kt-media-dead'), false, 'dead-pid own-schema root is swept')
    assert.equal(await exists('kt-media-live'), true, 'live owner root is preserved')
    assert.equal(await exists('kt-media-foreign'), true, 'foreign-schema root is preserved')
    assert.equal(await exists('kt-media-malformed'), true, 'malformed marker is preserved')
    assert.equal(await exists('kt-media-unowned'), true, 'unowned root is preserved')
    assert.equal(await exists('unrelated-dir'), true, 'arbitrary temp entry is preserved')
    assert.equal(await exists(path.basename(spool.root)), true, 'own root is preserved')
    const ownMarker = JSON.parse(await fs.readFile(path.join(spool.root, 'owner.json'), 'utf8'))
    assert.equal(ownMarker.schema, OWNER_SCHEMA)
    assert.equal(ownMarker.instanceId, 'self-instance')
    assert.equal(ownMarker.pid, 999)
  } finally {
    await cleanup(spool)
  }
})

test('dispose removes only the owning root and rejects later prepares', async () => {
  const base = await tmpBase()
  const spool = await makeSpool({ spoolBase: base, fetchImpl: async () => response({ body: bytesBody(Buffer.from('m')) }) })
  const root = spool.root
  assert.equal((await fs.readdir(base)).includes(path.basename(root)), true)
  await spool.dispose()
  assert.equal((await fs.readdir(base)).includes(path.basename(root)), false)
  await assert.rejects(spool.prepare(REF, { name: 'later.mp4' }), /disposed/)
})

test('a transport failure is reported without exposing a resource', async () => {
  const spool = await makeSpool({
    fetchImpl: async () => {
      throw Error('connection reset')
    },
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4' }), /media request failed/i)
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await cleanup(spool)
  }
})
