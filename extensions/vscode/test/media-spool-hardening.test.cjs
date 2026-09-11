const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { MediaSpool, MediaSpoolError } = require('../src/host/mediaSpool.cjs')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'

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

function response({ body, contentType = 'video/mp4' }) {
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? contentType : null) },
    body,
  }
}

async function makeSpool(overrides = {}) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'kt-spool-harden-'))
  const spool = new MediaSpool({
    base: 'http://127.0.0.1:8000',
    token: 'host-secret-token',
    spoolBase: base,
    fetchImpl: overrides.fetchImpl,
    asWebviewUri: (filePath) => `vscode-webview://spool/${path.basename(filePath)}`,
    limits: overrides.limits || {},
    fsImpl: overrides.fsImpl || fs,
    pathImpl: path,
    osImpl: os,
    cryptoImpl: require('node:crypto'),
    pid: process.pid,
    instanceId: overrides.instanceId || 'harden-instance',
    isProcessAlive: () => false,
  })
  await spool.start()
  return spool
}

test('a short disk write is completed in full before the next chunk is read', async () => {
  const events = []
  // Every write returns at most 3 bytes to force the completion loop.
  const shortFs = {
    ...fs,
    async open(file, flags) {
      const handle = await fs.open(file, flags)
      return {
        async write(buffer, offset = 0, length = buffer.length - offset) {
          const slice = buffer.subarray(offset, offset + Math.min(3, length))
          events.push('write')
          return handle.write(slice)
        },
        close: () => handle.close(),
      }
    },
  }
  const chunks = [Buffer.from('abcdefgh'), Buffer.from('ijklmnop')]
  const spool = await makeSpool({ fsImpl: shortFs, fetchImpl: async () => response({ body: trackedBody(chunks, events) }) })
  try {
    const resource = await spool.prepare(REF, { name: 'clip.mp4' })
    assert.equal(resource.bytes, 16)
    const stored = await fs.readFile(spool.resource(resource.resourceId).filePath)
    assert.equal(stored.toString(), 'abcdefghijklmnop')
    // 16 bytes at 3 bytes/write => more writes than chunks.
    assert.ok(events.filter((step) => step === 'write').length > chunks.length)
  } finally {
    await spool.dispose()
  }
})

test('the idle timeout also bounds a stalled initial response-header fetch', async () => {
  let fetched = false
  const spool = await makeSpool({
    limits: { idleTimeoutMs: 25 },
    fetchImpl: () => {
      fetched = true
      return new Promise(() => {})
    },
  })
  try {
    await assert.rejects(spool.prepare(REF, { name: 'clip.mp4' }), (error) => {
      assert.ok(error instanceof MediaSpoolError)
      assert.equal(error.kind, 'timeout')
      return true
    })
    assert.equal(fetched, true)
    assert.deepEqual(await spool.ownerRootEntries(), [])
  } finally {
    await spool.dispose()
  }
})

test('a prepared file keeps its original name suffix inside the spool root', async () => {
  const spool = await makeSpool({ fetchImpl: async () => response({ body: trackedBody([Buffer.from('m')], []) }) })
  try {
    const resource = await spool.prepare(REF, { name: '../../etc/passwd.mp4' })
    const stored = spool.resource(resource.resourceId)
    assert.equal(path.dirname(stored.filePath), spool.root)
    assert.match(path.basename(stored.filePath), /passwd\.mp4$/)
  } finally {
    await spool.dispose()
  }
})
