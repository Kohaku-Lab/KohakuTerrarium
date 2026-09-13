// The one fixed raw-file route a local ``file://`` media reference maps to.
// ``mediaFetchTarget`` must derive that route from the decoded local path, and a
// later validation pass must leave it untouched, so a compromised webview can
// never name its own fetch target. The path deliberately carries the exact
// reserved ``#?%`` characters plus a non-ASCII byte, so the percent-encoding the
// Host puts on the wire is pinned rather than assumed.
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { MediaSpool, MediaSpoolError } = require('../src/host/mediaSpool.cjs')
const { canonicalRawRoute, mediaFetchTarget } = require('../src/host/mediaPaths.cjs')

const TOKEN = 'host-secret-token'
const BASE = 'http://127.0.0.1:8000'
// The decoded local path an on-disk media reference names: reserved ``#?%`` plus
// a non-ASCII filename. Its exact percent-encoded route is what must hit the wire.
const RAW_PATH = 'C:/kt clips/a#b?c%d é.png'
const RAW_ROUTE = '/api/files/raw?path=C%3A%2Fkt%20clips%2Fa%23b%3Fc%25d%20%C3%A9.png'

function bytesBody(bytes) {
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

// A byte body with no declared length, recording each read so the no-cap stream
// test can prove the whole body is drained chunk by chunk.
function trackedBody(chunks, events) {
  let index = 0
  return {
    getReader() {
      return {
        async read() {
          events.push('read')
          if (index >= chunks.length) return { done: true, value: undefined }
          return { done: false, value: chunks[index++] }
        },
        async cancel() {},
      }
    },
  }
}

function response({ body, contentType = 'image/png', contentLength = null }) {
  return {
    ok: true,
    status: 200,
    headers: {
      get: (name) => (name.toLowerCase() === 'content-type' ? contentType : name.toLowerCase() === 'content-length' ? contentLength : null),
    },
    body,
  }
}

async function makeSpool(overrides = {}) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-raw-route-'))
  const spool = new MediaSpool({
    base: BASE,
    token: TOKEN,
    spoolBase: base,
    fetchImpl: overrides.fetchImpl || (async () => response({ body: bytesBody(Buffer.from('m')) })),
    asWebviewUri: (filePath) => `vscode-webview://spool/${path.basename(filePath)}`,
    limits: overrides.limits || {},
    fsImpl: fs,
    pathImpl: path,
    osImpl: os,
    cryptoImpl: require('node:crypto'),
    pid: process.pid,
    instanceId: overrides.instanceId || 'raw-route-instance',
    isProcessAlive: () => false,
  })
  await spool.start()
  return spool
}

test('mediaFetchTarget derives the exact percent-encoded raw route and stays idempotent', () => {
  assert.equal(mediaFetchTarget(RAW_PATH), RAW_ROUTE)
  assert.equal(canonicalRawRoute(RAW_ROUTE), RAW_ROUTE)
  assert.equal(mediaFetchTarget(RAW_ROUTE), RAW_ROUTE)
  // The path parameter decodes back to the byte-exact local path.
  assert.equal(decodeURIComponent(RAW_ROUTE.slice('/api/files/raw?path='.length)), RAW_PATH)
  // ``.`` and ``..`` in an absolute local path are delegated to the backend's own
  // resolution: the fixed ``/api/files/raw?path=`` query cannot name a second
  // fetch target, so the Host does not re-authorize the backend's filesystem.
  assert.equal(mediaFetchTarget('/tmp/../x'), '/api/files/raw?path=%2Ftmp%2F..%2Fx')
  assert.equal(mediaFetchTarget('/tmp/./x'), '/api/files/raw?path=%2Ftmp%2F.%2Fx')
  assert.equal(canonicalRawRoute('/api/files/raw?path=%2Ftmp%2Fa%2F..%2Fb.png'), '/api/files/raw?path=%2Ftmp%2Fa%2F..%2Fb.png')
  // A scheme URL, a relative path, and an empty value all yield no fetch target.
  for (const value of ['file:///etc/passwd', 'https://evil.example/x.png', 'relative/x.png', ''])
    assert.equal(mediaFetchTarget(value), null, value)
})

test('the spool fetches exactly the derived raw route with the host token and refuses a non-canonical target', async () => {
  const calls = []
  const spool = await makeSpool({
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return response({ body: bytesBody(Buffer.from('png-bytes')) })
    },
  })
  try {
    const route = mediaFetchTarget(RAW_PATH)
    const resource = await spool.prepare(route, { name: 'a#b?c%d é.png' })
    // The one request carries the fixed route verbatim and never leaks the token.
    assert.equal(calls[0].url, `${BASE}${RAW_ROUTE}`)
    assert.equal(calls[0].options.redirect, 'error')
    assert.equal(calls[0].options.headers['X-KT-Host-Token'], TOKEN)
    assert.equal(resource.uri.includes(TOKEN), false)
    assert.equal(resource.name, 'a#b?c%d é.png')
    // Path-hostile bytes never escape the private spool root.
    assert.equal(path.dirname(spool.resource(resource.resourceId).filePath), spool.root)
    // A webview cannot name its own target: the raw path itself, and a raw route
    // carrying an extra parameter, are both refused before any fetch.
    for (const bad of [RAW_PATH, '/api/files/raw?path=%2Ftmp%2Fa.mp4&x=1']) {
      await assert.rejects(spool.prepare(bad, { name: 'x.png' }), (error) => {
        assert.ok(error instanceof MediaSpoolError)
        assert.equal(error.kind, 'invalid')
        return true
      })
    }
    assert.equal(calls.length, 1)
  } finally {
    await spool.dispose()
  }
})

test('a >32 MiB unknown-length stream is spooled whole with no accidental single-file cap', async () => {
  const events = []
  const megabyte = Buffer.alloc(1 << 20, 9)
  const chunks = Array.from({ length: 33 }, () => megabyte)
  const spool = await makeSpool({
    fetchImpl: async () => response({ body: trackedBody(chunks, events), contentType: 'video/mp4' }),
  })
  try {
    const resource = await spool.prepare('/api/sessions/graph_1/artifacts/generated_videos/clip.mp4', { name: 'clip.mp4' })
    assert.equal(resource.bytes, 33 * (1 << 20))
    assert.match(resource.sha256, /^[0-9a-f]{64}$/)
    // Every chunk was read, so nothing short-circuited on a size ceiling.
    assert.equal(events.filter((step) => step === 'read').length, 34)
  } finally {
    await spool.dispose()
  }
})
