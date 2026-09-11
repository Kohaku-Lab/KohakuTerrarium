// Behaviour rewrite of the old artifact-view suite. The Extension-only
// ``artifactImages.mjs`` loader + MutationObserver was deleted in favour of the
// shared media resolver; these tests pin the replacement behaviour instead of
// being dropped, so the Host-mediated read still has coverage from this file.
//
// The resolver now owns a LOCAL per-consumer lease cache over the shared
// ``media.prepare`` transport read: overlapping consumers of one (owner, path)
// share a single authenticated read, but each owns a distinct lease handle so
// one consumer cancelling or releasing never tears down a read another consumer
// still displays.
const assert = require('node:assert/strict')
const test = require('node:test')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'

const tick = () => new Promise((resolve) => setImmediate(resolve))
const signal = () => ({ disposed: false, onCancel: null })

async function loadResolver() {
  return import('../src/webview/mediaResources.mjs')
}

test('the host resolver prepares a resource through media.prepare and exposes open/save/release', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(sent.length)
    if (type === 'media.prepare')
      return Promise.resolve({ resourceId: 'r1', uri: 'vscode-webview://spool/r1', name: data.name, mime: 'video/mp4' })
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 7, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 7 }),
  })
  const result = await resolver.resolveMedia(REF, { name: 'clip.mp4' })
  assert.ok(result.resourceId, 'a per-consumer lease handle is handed out')
  assert.equal(result.url, 'vscode-webview://spool/r1')
  assert.equal(result.name, 'clip.mp4')
  assert.equal(result.mime, 'video/mp4')
  assert.equal(result.bytes, undefined)
  const prep = sent.find((message) => message.type === 'media.prepare')
  assert.equal(prep.data.readyId, 7)
  assert.equal(prep.data.selectionVersion, 0)
  // open/save/release take the handle; the resolver translates it to the shared Host resourceId.
  await resolver.open(result.resourceId)
  await resolver.save(result.resourceId)
  await resolver.release(result.resourceId)
  assert.deepEqual(
    sent.map((message) => message.type),
    ['media.prepare', 'media.open', 'media.save', 'media.release'],
  )
  assert.equal(sent.find((message) => message.type === 'media.open').data.resourceId, 'r1')
  assert.equal(sent.find((message) => message.type === 'media.save').data.resourceId, 'r1')
  assert.equal(sent.find((message) => message.type === 'media.release').data.resourceId, 'r1')
})

test('an image part and a Markdown alt of one artifact share a single prepare with per-consumer labels', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const prepared = []
  const request = (type, data = {}, onSend = () => {}) => {
    onSend(1)
    if (type === 'media.prepare') {
      prepared.push(data)
      return Promise.resolve({ resourceId: 'r1', uri: 'vscode-webview://spool/r1', name: data.name, mime: 'image/png' })
    }
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
  })
  const image = await resolver.resolveImage(REF, { name: 'pic.png' })
  const markdown = await resolver.resolveImage(REF, { name: 'from markdown' })
  assert.equal(prepared.length, 1, 'name is not part of the dedupe identity')
  assert.equal(prepared[0].path, REF)
  assert.equal(image.url, 'vscode-webview://spool/r1')
  assert.equal(markdown.url, 'vscode-webview://spool/r1')
  assert.equal(image.name, 'pic.png')
  assert.equal(markdown.name, 'from markdown', 'each consumer keeps its own label')
  assert.notEqual(image.resourceId, markdown.resourceId, 'each consumer owns a distinct lease handle')
})

test('cancelling one shared consumer keeps the other read pending', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  const settles = []
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(sent.length)
    if (type === 'media.prepare') return new Promise((resolve) => settles.push(resolve))
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
  })
  const a = signal()
  const b = signal()
  const pa = resolver.resolveMedia(REF, { signal: a, name: 'image' })
  const pb = resolver.resolveMedia(REF, { signal: b, name: 'alt' })
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 1)
  // One consumer cancels while the read is still streaming: the shared read must survive.
  a.disposed = true
  a.onCancel()
  assert.equal(
    sent.some((message) => message.type === 'media.cancel'),
    false,
    'one cancel does not abort the shared read',
  )
  settles[0]({ resourceId: 'r1', uri: 'vscode-webview://spool/r1' })
  const result = await pb
  assert.equal(result.url, 'vscode-webview://spool/r1')
  await assert.rejects(() => pa, /ownership changed/i)
})

test('release one keeps the other URI; the last release evicts and releases the Host lease exactly once', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(sent.length)
    if (type === 'media.prepare') return Promise.resolve({ resourceId: 'r1', uri: 'vscode-webview://spool/r1', mime: 'image/png' })
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
  })
  const ra = await resolver.resolveMedia(REF, { signal: signal() })
  const rb = await resolver.resolveMedia(REF, { signal: signal() })
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 1)
  await resolver.release(ra.resourceId)
  assert.equal(
    sent.some((message) => message.type === 'media.release'),
    false,
    'one release keeps the shared resource',
  )
  assert.equal(rb.url, 'vscode-webview://spool/r1', 'the other consumer keeps its URI')
  await resolver.release(rb.resourceId)
  const releases = sent.filter((message) => message.type === 'media.release')
  assert.equal(releases.length, 1, 'the last release releases the Host lease exactly once')
  assert.equal(releases[0].data.resourceId, 'r1')
  // A duplicate release of an already-dropped handle is a no-op and cannot double-free.
  await resolver.release(ra.resourceId)
  assert.equal(sent.filter((message) => message.type === 'media.release').length, 1)
  // The cache entry was evicted, so the next resolve issues a fresh prepare.
  const rc = await resolver.resolveMedia(REF, { signal: signal() })
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 2, 'the next resolve is fresh')
  assert.equal(rc.url, 'vscode-webview://spool/r1')
})

test('a superseded generation late completion cannot evict the current cache entry', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  const settles = []
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(sent.length)
    if (type === 'media.prepare') return new Promise((resolve) => settles.push(resolve))
    return Promise.resolve({ ok: true })
  }
  let gen = 0
  const generation = {
    get value() {
      return gen
    },
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
    generation,
  })
  const first = signal()
  const p1 = resolver.resolveMedia(REF, { signal: first })
  // The generation flips and the old consumer goes away before its read settles.
  gen += 1
  first.disposed = true
  first.onCancel()
  // A fresh resolve creates the CURRENT entry for the same key while the stale read is still pending.
  const p2 = resolver.resolveMedia(REF, { signal: signal() })
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 2)
  // The old read finally settles: its cleanup must not delete the current entry.
  settles[0]({ resourceId: 'old', uri: 'vscode-webview://spool/old' })
  await assert.rejects(() => p1, /ownership changed/i)
  await tick()
  const p3 = resolver.resolveMedia(REF, { signal: signal() })
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 2, 'the current entry survived the stale cleanup')
  assert.ok(
    sent.some((message) => message.type === 'media.cancel' && message.data.resourceId === 'old'),
    'the stale spooled resource is discarded',
  )
  settles[1]({ resourceId: 'new', uri: 'vscode-webview://spool/new' })
  const latest = await p3
  assert.equal(latest.url, 'vscode-webview://spool/new')
  void p2
})

test('an active cancel with no consumers aborts even before the request is stamped', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  let stamp = null
  let settle = null
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    if (type === 'media.prepare') {
      stamp = onSend
      return new Promise((resolve) => (settle = resolve))
    }
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
  })
  const consumer = signal()
  const pending = resolver.resolveMedia(REF, { signal: consumer })
  consumer.disposed = true
  consumer.onCancel()
  assert.equal(
    sent.some((message) => message.type === 'media.cancel'),
    false,
    'nothing to abort until the requestId is known',
  )
  stamp(77)
  assert.ok(
    sent.some((message) => message.type === 'media.cancel' && message.data.prepareRequestId === 77),
    'the pending abort flushes on the first onSend',
  )
  settle({ resourceId: 'r1', uri: 'vscode-webview://spool/r1' })
  await assert.rejects(() => pending, /ownership changed/i)
  assert.ok(
    sent.some((message) => message.type === 'media.cancel' && message.data.resourceId === 'r1'),
    'the late spooled resource is discarded instead of orphaned',
  )
})

test('a failed prepare is evicted so the next resolve retries once, without a busy loop', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  let fail = true
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(sent.length)
    if (type === 'media.prepare')
      return fail ? Promise.reject(new Error('boom')) : Promise.resolve({ resourceId: 'r1', uri: 'vscode-webview://spool/r1' })
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => ({ readyId: 1 }),
  })
  await assert.rejects(() => resolver.resolveMedia(REF), /Could not load media/i)
  assert.equal(sent.filter((message) => message.type === 'media.prepare').length, 1, 'a failure is not retried on its own')
  fail = false
  const ok = await resolver.resolveMedia(REF)
  assert.equal(
    sent.filter((message) => message.type === 'media.prepare').length,
    2,
    'the failed entry was evicted so the next resolve is fresh',
  )
  assert.equal(ok.url, 'vscode-webview://spool/r1')
})

test('cancelling mid-flight sends media.cancel with the prepareRequestId, then discards the late resource', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  let settle
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(41)
    if (type === 'media.prepare') return new Promise((resolve) => (settle = resolve))
    return Promise.resolve({ ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
  })
  const consumer = signal()
  const pending = resolver.resolveMedia(REF, { signal: consumer })
  assert.equal(typeof consumer.onCancel, 'function')
  // The consumer cancels while the read is still streaming.
  consumer.disposed = true
  consumer.onCancel()
  assert.ok(
    sent.some((message) => message.type === 'media.cancel' && message.data.prepareRequestId === 41),
    'active abort carries the prepare requestId',
  )
  // A late settle must not expose the resource: it is discarded Host-side.
  settle({ resourceId: 'r9', uri: 'vscode-webview://spool/r9' })
  await assert.rejects(() => pending, /ownership changed/i)
  assert.ok(
    sent.some((message) => message.type === 'media.cancel' && message.data.resourceId === 'r9'),
    'late resource is released after settle',
  )
})

test('an ownership flip after the response discards the late resource', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const sent = []
  let owner = 'a'
  const request = (type, data = {}, onSend = () => {}) => {
    sent.push({ type, data })
    onSend(1)
    return Promise.resolve(type === 'media.prepare' ? { resourceId: 'r2', uri: 'x' } : { ok: true })
  }
  const resolver = createHostMediaResolver({
    request,
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    getOwner: () => owner,
  })
  const pending = resolver.resolveMedia(REF)
  owner = 'b'
  await assert.rejects(() => pending, /ownership changed/i)
  assert.ok(sent.some((message) => message.type === 'media.cancel' && message.data.resourceId === 'r2'))
})

test('without a ready fence the resolver refuses instead of issuing a rejected request', async () => {
  const { createHostMediaResolver } = await loadResolver()
  let sent = 0
  const resolver = createHostMediaResolver({
    request: () => {
      sent += 1
      return Promise.resolve({})
    },
    getFence: () => null,
  })
  await assert.rejects(() => resolver.resolveMedia(REF), /ready before loading media/i)
  assert.equal(sent, 0)
})

test('the webview resolver keeps data/blob images and drops external or file schemes', async () => {
  const { createHostMediaResolver } = await loadResolver()
  const resolver = createHostMediaResolver({
    request: () => Promise.resolve({}),
    getFence: () => ({ readyId: 1, selectionVersion: 0 }),
  })
  assert.equal(resolver.resolveImage('data:image/png;base64,AAAA'), 'data:image/png;base64,AAAA')
  assert.equal(resolver.resolveImage('blob:https://app.test/x'), 'blob:https://app.test/x')
  assert.equal(resolver.resolveImage('https://evil.example/x.png'), '')
  assert.equal(resolver.resolveImage('file:///etc/passwd'), '')
})
