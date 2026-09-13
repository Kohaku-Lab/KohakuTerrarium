// Host-side media coordinator. It owns the bounded spool and the resource
// lifecycle the webview drives: prepare (fetch -> spool -> selection-ready
// fence), release (lease decrement), cancel/discard, open (editor tab lease),
// and save (copy to a user-chosen destination). Adapters are injected so the
// module is testable without the ``vscode`` API.
const { mediaFetchTarget } = require('./mediaPaths.cjs')

// A cancel can arrive before its ``media.prepare`` does, so the Host remembers it
// to reject that later prepare. The record is consumed by the matching prepare and
// only exists for the window before the prepare is seen, so it is bounded: a
// requestId is unique per request and a stale record is dropped oldest-first, and
// a fresh (distinct) requestId can never be refused by a pruned record.
const MAX_CANCELLED_REQUESTS = 256

class MediaHost {
  constructor({ spool = null, openEditor = null, saveAs = null, onEditorLeaseChange = null } = {}) {
    this.spool = spool
    this.openEditor = openEditor
    this.saveAs = saveAs
    // Notified with the editor lease count whenever it changes, so a View can start
    // (and stop) its tab-close reconciliation without polling.
    this.onEditorLeaseChange = onEditorLeaseChange
    // In-flight prepares keyed by the originating requestId, so a cancel that
    // races ahead of the prepared resourceId can still abort the transport.
    this.controllers = new Map()
    // Cancel-before-prepare records: requestId -> true, insertion-ordered and
    // bounded by ``MAX_CANCELLED_REQUESTS`` (see ``rememberCancelled``).
    this.cancelledRequests = new Map()
    this.editorResources = new Map()
    this.disposed = false
  }

  async start() {
    if (!this.spool) throw Error('Media spool is unavailable')
    return this.spool.start()
  }

  // The stable target identity + explicit-intent fence. The Host never exposes a
  // resource to the webview before it is fully spooled (see ``prepare``).
  fence(runtime, message) {
    const selected = runtime?.state?.selection
    if (!selected) throw Error('Select a Creature before reading media')
    if (!runtime.ownsArtifactRead(selected, message)) throw Error('Selected Creature ownership changed')
    if (runtime.pendingSelectionMutations > 0) throw Error('Selected Creature ownership changed')
  }

  async handle(message, runtime) {
    if (this.disposed) throw Error('Media spool is disposed')
    switch (message.type) {
      case 'media.prepare':
        return this.prepare(message, runtime)
      case 'media.release':
        return this.releaseOwned(message)
      case 'media.cancel':
        return this.cancelOwned(message)
      case 'media.open':
        return this.openOwned(message)
      case 'media.save':
        return this.saveOwned(message)
      default:
        throw Error('Unsupported media request')
    }
  }

  async prepare(message, runtime) {
    if (!this.spool) throw Error('Media spool is unavailable')
    this.fence(runtime, message)
    // The Host derives the one fixed same-origin route (artifact route or raw-file
    // route) from the reference; a webview-supplied absolute URL or traversal path
    // never reaches the network.
    const target = mediaFetchTarget(message.path)
    if (!target) throw Error('Unknown media reference')
    const requestId = message.requestId
    // A cancel that landed before this prepare is honored exactly once, then the
    // record is dropped so it can never affect any other request.
    if (this.cancelledRequests.has(requestId)) {
      this.cancelledRequests.delete(requestId)
      throw Error('Media request cancelled')
    }
    const controller = new AbortController()
    this.controllers.set(requestId, controller)
    try {
      // Only the fully spooled fence fields are returned; the disk path and token
      // stay Host-only.
      return await this.spool.prepare(target, { signal: controller.signal, name: message.name })
    } finally {
      this.controllers.delete(requestId)
    }
  }

  releaseOwned(message) {
    if (!this.spool) throw Error('Media spool is unavailable')
    return { ok: this.spool.release(message.resourceId, message.lease || 'webview') }
  }

  // Remember a requestId whose prepare has not been seen yet, oldest-first bounded.
  rememberCancelled(requestId) {
    this.cancelledRequests.delete(requestId)
    this.cancelledRequests.set(requestId, true)
    while (this.cancelledRequests.size > MAX_CANCELLED_REQUESTS) {
      this.cancelledRequests.delete(this.cancelledRequests.keys().next().value)
    }
  }

  cancelOwned(message) {
    if (!this.spool) throw Error('Media spool is unavailable')
    if (Number.isSafeInteger(message.prepareRequestId)) {
      const controller = this.controllers.get(message.prepareRequestId)
      // An in-flight prepare is aborted directly (its own failure path discards the
      // resource); only a cancel that arrives before the prepare is recorded, so
      // the record set never grows with completed requests.
      if (controller) controller.abort()
      else this.rememberCancelled(message.prepareRequestId)
    }
    if (typeof message.resourceId === 'string' && message.resourceId) {
      return { ok: this.spool.discard(message.resourceId) }
    }
    return { ok: true }
  }

  async openOwned(message) {
    if (!this.openEditor) throw Error('Opening media is unavailable')
    const resource = this.spool?.resource(message.resourceId)
    if (!resource) throw Error('Unknown media resource')
    // Re-opening a resource that already drives a tab must not stack editor leases:
    // one file path maps to one resource, so one tab close reclaims it. Acquire the
    // editor lease before opening so the panel can dispose first without the file
    // being deleted while the editor tab is still open.
    const alreadyOpen = this.editorResources.get(resource.filePath) === message.resourceId
    if (!alreadyOpen) this.spool.acquire(message.resourceId, 'editor')
    try {
      await this.openEditor(resource)
    } catch (error) {
      if (!alreadyOpen) this.spool.release(message.resourceId, 'editor')
      throw error
    }
    this.editorResources.set(resource.filePath, message.resourceId)
    this.notifyEditorLeases()
    return { ok: true }
  }

  async saveOwned(message) {
    if (!this.saveAs) throw Error('Saving media is unavailable')
    const resource = this.spool?.resource(message.resourceId)
    if (!resource) throw Error('Unknown media resource')
    // The adapter reports whether a file was actually written (``{ saved: true }``);
    // a dismissed Save dialog must be distinguishable from a completed save.
    const result = await this.saveAs(resource)
    const saved = result === true || result?.saved === true
    return saved ? { ok: true, cancelled: false } : { ok: false, cancelled: true }
  }

  // Reconcile an editor tab that closed: drop its editor lease so the file can
  // finally be reclaimed once the webview has also released it.
  closeEditor(filePath) {
    const resourceId = this.editorResources.get(filePath)
    if (resourceId === undefined) return false
    this.editorResources.delete(filePath)
    const released = this.spool?.release(resourceId, 'editor') ?? false
    this.notifyEditorLeases()
    return released
  }

  editorFilePaths() {
    return [...this.editorResources.keys()]
  }

  notifyEditorLeases() {
    this.onEditorLeaseChange?.(this.editorResources.size)
  }

  // Called when an explicit selection intent supersedes in-flight reads.
  abortAll() {
    for (const controller of this.controllers.values()) controller.abort()
  }

  // View dispose: abort in-flight prepares and drop the webview lease surface, but
  // keep files an open editor tab still holds AND the path -> resource mapping, so a
  // tab close that lands after the webview is gone still reconciles the editor lease.
  // The extension's ``dispose`` (deactivate) is what finally drops everything.
  releaseView() {
    this.abortAll()
    this.spool?.releaseAll('webview')
  }

  async dispose() {
    this.disposed = true
    this.abortAll()
    this.controllers.clear()
    this.cancelledRequests.clear()
    this.editorResources.clear()
    if (this.spool) await this.spool.dispose()
  }
}

// The Runtime routes every ``media.*`` message here: the coordinator handles it
// (or fails honestly when none is wired) and the one fixed result envelope is
// posted. Kept beside the coordinator so RuntimeHost stays a cohesive host
// lifecycle instead of a message switch that also knows the media surface.
const MEDIA_TYPES = new Set(['media.prepare', 'media.release', 'media.cancel', 'media.open', 'media.save'])

async function dispatchMedia(runtime, message) {
  if (!runtime.mediaHost) throw Error('Media is unavailable')
  const data = await runtime.mediaHost.handle(message, runtime)
  runtime.post({ type: `${message.type}.result`, requestId: message.requestId, data })
}

module.exports = { MediaHost, MEDIA_TYPES, dispatchMedia, MAX_CANCELLED_REQUESTS }
