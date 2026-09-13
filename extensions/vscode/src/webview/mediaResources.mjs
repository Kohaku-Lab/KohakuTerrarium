// VS Code webview media resolver: a thin adapter that turns a shared-component
// media reference into a Host ``media.prepare`` request and exposes the
// Host-spooled webview URI plus explicit open/save. It speaks only through the
// existing ``request`` demux, carries the ready/selection fence the Host gates
// on, and owns the LOCAL lease bookkeeping for a shared read — a transport
// resource-ownership cache, never chat business state. Error text comes from the
// shared locale dictionary (``translate``), never a private VS Code-only string
// table.
//
// Dedupe identity is ``owner + path``: the ``<img>`` part and the Markdown copy
// of the same artifact must resolve to ONE authenticated read even though the
// two consumers carry different display labels (alt text vs file name). Each
// consumer still owns exactly one lease handle, so one consumer cancelling or
// releasing never tears a read another consumer is still displaying.
const DATA_OR_BLOB = /^(data:|blob:)/i
const SCHEME = /^[a-z][a-z\d+.-]*:/i

export function createHostMediaResolver(options = {}) {
  const { request, getFence, filePathOf } = options
  const ownerKey = () => (typeof options.getOwner === 'function' ? JSON.stringify(options.getOwner()) : null)
  // A ``file://`` reference is media a tool looked at rather than produced. It is
  // not a same-origin route, so the Host cannot fetch it directly; the shared
  // ``fileReferencePath`` helper (injected by the bridge) turns it into the local
  // path the Host canonicalizes into its one fixed raw-file route. Kept as an
  // injected seam so this module stays free of the shared package and directly
  // importable by the unit tests.
  const localPath = (value) => (typeof filePathOf === 'function' ? filePathOf(value) : '')

  const api = {
    kind: 'host',
    canOpen: true,
    canSave: true,
    // ``translate`` is bound at creation; ``onError`` may be assigned after
    // creation by the caller without rebuilding the resolver.
    translate: options.translate,
    onError: options.onError,
    // Reactive ready/selection generation supplied by the host bridge. When it
    // flips the dedupe cache is dropped, so an unchanged reference re-resolves
    // against the new fence instead of replaying a stale spooled URI.
    generation: options.generation,
  }

  const say = (key, params, fallback) => {
    const text = typeof api.translate === 'function' ? api.translate(key, params) : fallback
    return text && text !== key ? text : fallback
  }
  const report = (error) => {
    if (typeof api.onError === 'function') api.onError(error)
  }
  const send = (type, data) => request(type, data).catch(report)

  // One Host ``media.prepare`` is shared by every consumer of an (owner, path);
  // each consumer still receives a DISTINCT opaque lease handle. ``release`` /
  // ``open`` / ``save`` are driven by that handle, so a duplicate release of one
  // container cannot tear down a resource another container still displays, and
  // the adapter can translate a handle back to the single shared Host resourceId
  // (no protocol change).
  let leaseSeq = 0
  const handles = new Map() // handle id -> lease

  // The resolved cache is dropped when the generation flips, so a Refresh never
  // reuses the previous epoch's resource. A failed read is never cached (a later
  // render retries once, with no busy retry loop).
  const cache = new Map() // `${owner}|${path}` -> entry
  let cacheGeneration = api.generation ? api.generation.value : undefined
  const syncGeneration = () => {
    if (!api.generation) return
    const value = api.generation.value
    if (value !== cacheGeneration) {
      cacheGeneration = value
      cache.clear()
    }
  }

  // Never delete a newer entry that now owns the same key: a late completion
  // from a superseded generation must clean up only its OWN entry.
  const evict = (entry) => {
    if (cache.get(entry.key) === entry) cache.delete(entry.key)
  }
  // Abandon a spooled resource nobody will display (cancelled/superseded).
  const discard = (entry) => {
    if (entry.discarded || !entry.hostResourceId) return
    entry.discarded = true
    send('media.cancel', { resourceId: entry.hostResourceId })
  }
  // Decrement the single Host lease the moment the LAST consumer lets go.
  const releaseHost = (entry) => {
    if (entry.released || !entry.hostResourceId) return
    entry.released = true
    send('media.release', { resourceId: entry.hostResourceId })
  }
  // Cancellation is ACTIVE, not post-settle: abort the in-flight prepare by its
  // requestId. A cancel that lands before the request is stamped flushes on the
  // first ``onSend``.
  const abort = (entry) => {
    if (entry.settled) return
    entry.abortRequested = true
    if (entry.requestId != null) send('media.cancel', { prepareRequestId: entry.requestId })
  }

  // Drop one consumer's lease. The shared Host read is torn down only when the
  // LAST consumer lets go: abort while pending, otherwise release the single
  // Host lease once (or discard it if it was never displayed) and evict the
  // cache entry so the next resolve re-prepares against a fresh resource.
  const closeLease = (lease, { discardHost = false } = {}) => {
    if (lease.closed) return
    lease.closed = true
    lease.active = false
    handles.delete(lease.id)
    const { entry } = lease
    entry.consumers.delete(lease)
    if (entry.consumers.size > 0) return
    if (!entry.settled) abort(entry)
    else if (discardHost) discard(entry)
    else releaseHost(entry)
    evict(entry)
  }

  const createEntry = (key, path, fence, name) => {
    const entry = {
      key,
      consumers: new Set(),
      requestId: null,
      settled: false,
      hostResourceId: null,
      released: false,
      discarded: false,
      abortRequested: false,
    }
    entry.promise = request(
      'media.prepare',
      {
        path,
        ...(name ? { name } : {}),
        readyId: fence.readyId,
        selectionVersion: fence.selectionVersion,
      },
      (id) => {
        entry.requestId = id
        if (entry.abortRequested) send('media.cancel', { prepareRequestId: id })
      },
    )
    cache.set(key, entry)
    entry.promise.then(
      (result) => {
        entry.settled = true
        entry.hostResourceId = result?.resourceId || null
        // Abandoned before it settled: never orphan the spooled resource, and
        // never delete a newer entry that now occupies this key.
        if (entry.abortRequested || entry.consumers.size === 0) {
          discard(entry)
          evict(entry)
        }
      },
      () => {
        entry.settled = true
        evict(entry)
      },
    )
    return entry
  }

  async function prepare(path, { name, signal } = {}) {
    syncGeneration()
    const fence = getFence?.()
    // Without a live ready/selection fence the Host refuses the read; surface
    // the same honest state instead of issuing a request that would be rejected.
    if (!fence) throw Error(say('chat.media.notReady', {}, 'Wait for the Session to be ready before loading media'))
    const owned = ownerKey()
    const key = `${owned}|${path}`
    const entry = cache.get(key) || createEntry(key, path, fence, name)
    const lease = { id: `kt-lease-${++leaseSeq}`, entry, signal, active: true, closed: false }
    entry.consumers.add(lease)
    handles.set(lease.id, lease)
    if (signal) {
      // Only the last consumer cancelling aborts the shared read, so one leaf
      // unmounting never aborts a read another leaf is still awaiting.
      signal.onCancel = () => closeLease(lease, { discardHost: true })
    }
    let result
    try {
      result = await entry.promise
    } catch {
      closeLease(lease)
      throw Error(say('chat.media.prepareFailed', { name: name || 'media' }, 'Could not load media'))
    } finally {
      if (signal) signal.onCancel = null
    }
    if (!lease.active || owned !== ownerKey()) {
      // Cancelled or superseded around the response: never hand out a lease, and
      // discard the spooled resource rather than leaving it orphaned.
      closeLease(lease, { discardHost: true })
      throw Error(say('chat.media.superseded', {}, 'Media ownership changed'))
    }
    // Labels are per-consumer even though the read is shared; the URI/mime/bytes
    // come from the one Host resource.
    return {
      resourceId: lease.id,
      url: result?.uri || '',
      name: name || result?.name || '',
      mime: result?.mime || '',
      bytes: result?.bytes,
    }
  }

  const hostResourceId = (handleId) => handles.get(handleId)?.entry?.hostResourceId || ''

  api.resolveImage = (value, opts = {}) => {
    // The webview CSP blocks arbitrary network origins; only Host-mediated
    // artifact/raw-file references and self-contained data/blob images resolve.
    if (DATA_OR_BLOB.test(value)) return value
    if (SCHEME.test(value) || value.startsWith('//')) {
      // A ``file://`` reference maps to the local path the Host streams (its own
      // fixed raw-file route); any other scheme is not displayable and is dropped.
      const path = localPath(value)
      return path ? prepare(path, opts) : ''
    }
    return prepare(value, opts)
  }
  api.resolveMedia = (value, opts = {}) => {
    if (DATA_OR_BLOB.test(value)) return value
    return prepare(localPath(value) || value, opts)
  }
  // ``release``/``open``/``save`` take the per-consumer handle this resolver
  // handed out; open/save translate it to the shared Host resourceId.
  api.release = (handleId) => {
    const lease = handles.get(handleId)
    if (lease) closeLease(lease)
    return Promise.resolve()
  }
  api.open = (handleId) => {
    const resourceId = hostResourceId(handleId)
    return resourceId ? send('media.open', { resourceId }) : Promise.resolve()
  }
  api.save = (handleId) => {
    const resourceId = hostResourceId(handleId)
    return resourceId ? send('media.save', { resourceId }) : Promise.resolve()
  }
  return api
}
