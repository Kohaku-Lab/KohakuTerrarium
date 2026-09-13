// REAL HTTP loopback suite: drives the spool against a live Node HTTP server and
// the real global fetch (no mocked transport), so the exact wire bytes, the
// redirect refusal, a mid-body disconnect, and a >32 MiB unknown-length body are
// pinned end to end. Each server is isolated to an ephemeral 127.0.0.1 port and
// closed in teardown, so no listener or timer survives the suite.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { MediaSpool, MediaSpoolError } = require('../src/host/mediaSpool.cjs')
const { mediaFetchTarget } = require('../src/host/mediaPaths.cjs')

const TOKEN = 'host-secret-token'
const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'

async function listen(handler) {
  const server = http.createServer(handler)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    origin: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

async function makeSpool(base, overrides = {}) {
  const spoolBase = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-real-http-'))
  const spool = new MediaSpool({
    base,
    token: TOKEN,
    spoolBase,
    fetchImpl: fetch, // the real global fetch
    asWebviewUri: (filePath) => `vscode-webview://spool/${path.basename(filePath)}`,
    limits: overrides.limits || {},
    instanceId: overrides.instanceId || 'real-http-instance',
    isProcessAlive: () => false,
  })
  await spool.start()
  return spool
}

test('the real wire carries the exact percent-encoded #?% unicode route and the host token', async () => {
  const seen = []
  const server = await listen((req, res) => {
    seen.push({ url: req.url, token: req.headers['x-kt-host-token'] })
    res.writeHead(200, { 'content-type': 'image/png', 'content-length': '4' })
    res.end(Buffer.from('png!'))
  })
  const RAW_PATH = 'C:/kt clips/a#b?c%d é.png'
  const route = mediaFetchTarget(RAW_PATH)
  const spool = await makeSpool(server.origin)
  try {
    const resource = await spool.prepare(route, { name: 'a#b?c%d é.png' })
    assert.equal(seen.length, 1)
    // The exact encoded route, byte-for-byte, reaches the server: reserved ``#?%``
    // stay encoded and the non-ASCII byte is UTF-8 percent-encoded.
    assert.equal(seen[0].url, '/api/files/raw?path=C%3A%2Fkt%20clips%2Fa%23b%3Fc%25d%20%C3%A9.png')
    // The Host token travels only on the Host -> backend hop.
    assert.equal(seen[0].token, TOKEN)
    assert.equal(resource.uri.includes(TOKEN), false)
    assert.equal(resource.bytes, 4)
    assert.equal(resource.mime, 'image/png')
  } finally {
    await spool.dispose()
    await server.close()
  }
})

test('a 302 redirect is refused and the destination never receives the token', async () => {
  const seenDestination = []
  const destination = await listen((req, res) => {
    seenDestination.push({ url: req.url, token: req.headers['x-kt-host-token'] })
    res.writeHead(200, { 'content-type': 'image/png' })
    res.end('stolen')
  })
  const seenOrigin = []
  const origin = await listen((req, res) => {
    seenOrigin.push({ url: req.url })
    res.writeHead(302, { location: `${destination.origin}/steal` })
    res.end()
  })
  const spool = await makeSpool(origin.origin)
  try {
    await assert.rejects(spool.prepare(REF, { name: 'redir.png' }), (error) => {
      assert.ok(error instanceof MediaSpoolError)
      assert.equal(error.kind, 'transport')
      return true
    })
    assert.equal(seenOrigin.length, 1, 'the origin was contacted once')
    assert.equal(seenDestination.length, 0, 'the redirect destination is never contacted')
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await spool.dispose()
    await origin.close()
    await destination.close()
  }
})

test('a mid-body disconnect fails cleanly and removes the partial spool file', async () => {
  const server = await listen((req, res) => {
    res.writeHead(200, { 'content-type': 'video/mp4', 'content-length': String(4 << 20) })
    res.write(Buffer.alloc(64 * 1024, 1))
    // Reset the connection long before the declared length arrives.
    setTimeout(() => res.destroy(), 25)
  })
  const spool = await makeSpool(server.origin, { limits: { idleTimeoutMs: 2_000 } })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4' }), (error) => {
      assert.ok(error instanceof MediaSpoolError)
      assert.equal(error.kind, 'transport')
      return true
    })
    assert.deepEqual(await spool.ownerRootEntries(), [], 'the partial file is removed')
  } finally {
    await spool.dispose()
    await server.close()
  }
})

test('an unknown-length live response over 32 MiB is spooled whole with no default cap', async () => {
  const MIB = 1 << 20
  const total = 33 * MIB
  const server = await listen((req, res) => {
    // No content-length: the body is framed chunked and its size is unknown up front.
    res.writeHead(200, { 'content-type': 'video/mp4' })
    const chunk = Buffer.alloc(MIB, 9)
    let written = 0
    const pump = () => {
      while (written < total) {
        const room = total - written
        const slice = room >= MIB ? chunk : chunk.subarray(0, room)
        written += slice.length
        if (!res.write(slice)) {
          res.once('drain', pump)
          return
        }
      }
      res.end()
    }
    pump()
  })
  const spool = await makeSpool(server.origin)
  try {
    const resource = await spool.prepare(REF, { name: 'big.mp4' })
    assert.equal(resource.bytes, total)
    assert.match(resource.sha256, /^[0-9a-f]{64}$/)
    assert.deepEqual(await spool.ownerRootEntries().then((entries) => entries.length), 1)
  } finally {
    await spool.dispose()
    await server.close()
  }
})
