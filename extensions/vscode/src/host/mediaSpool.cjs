// Host-mediated media spool: authenticated fetch -> bounded private temp disk
// spool -> localResourceRoots -> webview.asWebviewUri. Heap stays ~O(chunk):
// the body is streamed to disk with backpressure and never buffered whole.
//
// There is deliberately NO default single-file size cap; only the number of
// concurrently ACTIVE spool requests is bounded. A request beyond that bound is
// not refused: it waits in a FIFO queue and starts the instant an active buffer
// frees, so a burst of media never leaves later images permanently missing. The
// queue is owned by the spool lifecycle: cancelling the caller's signal, an
// ``abortAll``/ready abort, or ``dispose`` removes a queued entry and settles it
// before its fetch would ever start. A caller may pass ``limits.maxBytes`` to opt
// into a hard oversize error (no fallback, no partial success).
const { mediaFetchTarget } = require('./mediaPaths.cjs')

const DEFAULT_LIMITS = Object.freeze({
  maxConcurrent: 4,
  // Per-chunk idle timeout: fired only when the stream stalls, not a whole-body budget.
  // It also bounds the initial response-header fetch, so a server that accepts the
  // connection but never sends headers cannot hang the spool forever. Because it
  // always settles an active buffer, a stalled fetch can never wedge the queue.
  idleTimeoutMs: 15_000,
})

const OWNER_SCHEMA = 'kt-media-owner/v1'
const OWNER_FILE = 'owner.json'
const ROOT_PREFIX = 'kt-media-'

class MediaSpoolError extends Error {
  constructor(kind, message, { code } = {}) {
    super(message)
    this.name = 'MediaSpoolError'
    this.kind = kind
    if (code) this.code = code
  }
}

function defaultAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    // EPERM still means a live process we are not allowed to signal.
    return error?.code === 'EPERM'
  }
}

function isDiskError(error) {
  return typeof error?.code === 'string' && /^(EACCES|ENOSPC|EROFS|EDQUOT|EMFILE|ENFILE|EIO|EPERM)$/.test(error.code)
}

class MediaSpool {
  constructor({
    base,
    token = '',
    fetchImpl = fetch,
    spoolBase,
    asWebviewUri = (filePath) => filePath,
    limits = {},
    fsImpl = require('node:fs/promises'),
    pathImpl = require('node:path'),
    osImpl = require('node:os'),
    cryptoImpl = require('node:crypto'),
    pid = process.pid,
    now = Date.now,
    instanceId = null,
    isProcessAlive = defaultAlive,
  } = {}) {
    this.base = base
    this.token = token
    this.fetchImpl = fetchImpl
    this.spoolBase = spoolBase || osImpl.tmpdir()
    this.asWebviewUri = asWebviewUri
    this.limits = { ...DEFAULT_LIMITS, ...limits }
    this.fs = fsImpl
    this.path = pathImpl
    this.crypto = cryptoImpl
    this.pid = pid
    this.now = now
    this.instanceId = instanceId || cryptoImpl.randomUUID()
    this.isAlive = isProcessAlive
    this.root = null
    this.resources = new Map()
    this.controllers = new Map()
    this.pending = []
    this.inFlight = 0
    // FIFO waiters for an active buffer: { resolve, reject, signal, onAbort }.
    this.waiters = []
    this.disposed = false
  }

  async start() {
    this.root = await this.fs.mkdtemp(this.path.join(this.spoolBase, ROOT_PREFIX))
    const marker = { schema: OWNER_SCHEMA, instanceId: this.instanceId, pid: this.pid, startedAt: this.now() }
    await this.fs.writeFile(this.path.join(this.root, OWNER_FILE), JSON.stringify(marker))
    await this.sweep()
    return this.root
  }

  async readMarker(root) {
    try {
      const parsed = JSON.parse(await this.fs.readFile(this.path.join(root, OWNER_FILE), 'utf8'))
      return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
      return null
    }
  }

  // Remove only our-schema roots whose owner pid is dead. Never touch our own
  // root, a live instance's root, a foreign/unknown-schema root, an unowned or
  // malformed entry, or an arbitrary temp entry.
  async sweep() {
    let entries
    try {
      entries = await this.fs.readdir(this.spoolBase, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const isDir = entry.isDirectory ? entry.isDirectory() : entry.isDirectory === undefined
      if (!isDir || !entry.name.startsWith(ROOT_PREFIX)) continue
      const root = this.path.join(this.spoolBase, entry.name)
      if (root === this.root) continue
      const marker = await this.readMarker(root)
      if (!marker || marker.schema !== OWNER_SCHEMA) continue
      if (marker.instanceId === this.instanceId) continue
      if (this.isAlive(marker.pid)) continue
      await this.removeRoot(root)
    }
  }

  queueRemove(filePath) {
    const task = this.safeRemove(filePath)
    this.pending.push(task)
    task.finally(() => {
      const index = this.pending.indexOf(task)
      if (index >= 0) this.pending.splice(index, 1)
    })
    return task
  }

  safeRemove(filePath) {
    return Promise.resolve(this.fs.rm(filePath, { force: true })).catch(() => {})
  }

  removeRoot(root) {
    return Promise.resolve(this.fs.rm(root, { recursive: true, force: true })).catch(() => {})
  }

  // Keep the caller's own filename as a suffix so Save/open preserve the
  // extension, but never let it escape the spool root.
  fileNameFor(resourceId, name) {
    const base =
      typeof name === 'string'
        ? this.path
            .basename(name)
            .replace(/[^A-Za-z0-9._-]/g, '_')
            .replace(/^\.+/, '')
        : ''
    const safe = base.slice(0, 96)
    return safe ? `${resourceId}-${safe}` : resourceId
  }

  // Take one of the ``maxConcurrent`` active buffers, or a FIFO position. Resolves
  // once a buffer is held (and the counter incremented); rejects with ``cancel`` if
  // the caller's signal aborts while queued, or ``disposed`` if the spool is torn
  // down first. A queued request that settles here never reaches the network.
  acquireSlot(signal) {
    if (this.disposed) throw new MediaSpoolError('disposed', 'Media spool disposed')
    if (signal?.aborted) throw new MediaSpoolError('cancel', 'Media request cancelled')
    if (this.inFlight < this.limits.maxConcurrent) {
      this.inFlight++
      return Promise.resolve()
    }
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, signal, onAbort: null }
      const drop = () => {
        const index = this.waiters.indexOf(waiter)
        if (index >= 0) this.waiters.splice(index, 1)
      }
      if (signal) {
        waiter.onAbort = () => {
          drop()
          reject(new MediaSpoolError('cancel', 'Media request cancelled'))
        }
        signal.addEventListener('abort', waiter.onAbort, { once: true })
      }
      this.waiters.push(waiter)
    })
  }

  // Free an active buffer and hand it to the oldest still-waiting request. A
  // waiter whose signal aborted is skipped without consuming the buffer.
  releaseSlot() {
    if (this.inFlight > 0) this.inFlight--
    while (this.inFlight < this.limits.maxConcurrent && this.waiters.length > 0) {
      const waiter = this.waiters.shift()
      if (waiter.onAbort) waiter.signal?.removeEventListener('abort', waiter.onAbort)
      if (waiter.signal?.aborted) {
        waiter.reject(new MediaSpoolError('cancel', 'Media request cancelled'))
        continue
      }
      this.inFlight++
      waiter.resolve()
    }
  }

  async prepare(canonicalPath, { signal, name } = {}) {
    if (this.disposed || !this.root) throw new MediaSpoolError('disposed', 'Media spool disposed')
    if (signal?.aborted) throw new MediaSpoolError('cancel', 'Media request cancelled')
    // The Host derives one FIXED same-origin route per reference (artifact route or
    // the raw-file route); the spool re-canonicalizes so only a Host-derived target
    // ever reaches the network, never a webview-supplied fetch target.
    const target = mediaFetchTarget(canonicalPath)
    if (!target || target !== canonicalPath) throw new MediaSpoolError('invalid', 'Unknown media reference')
    // Queue behind the active-buffer bound instead of failing fast; the slot is
    // released in the ``finally`` below, on every success and every failure.
    await this.acquireSlot(signal)
    const resourceId = this.crypto.randomUUID()
    const filePath = this.path.join(this.root, this.fileNameFor(resourceId, name))
    const controller = new AbortController()
    const onExternal = () => controller.abort()
    signal?.addEventListener('abort', onExternal, { once: true })
    if (signal?.aborted) controller.abort()
    this.controllers.set(resourceId, controller)
    try {
      const spooled = await this.fetchToDisk(target, filePath, controller, signal)
      if (this.disposed) throw new MediaSpoolError('disposed', 'Media spool disposed')
      const resource = {
        resourceId,
        uri: this.asWebviewUri(filePath, this.path.basename(filePath)),
        bytes: spooled.bytes,
        mime: spooled.mime,
        sha256: spooled.sha256,
        name: name || `${resourceId}`,
        state: 'exposed',
        leases: { webview: 1, editor: 0 },
        filePath,
      }
      this.resources.set(resourceId, resource)
      // Report only the fence fields the webview needs, never the disk path.
      return {
        resourceId,
        uri: resource.uri,
        bytes: resource.bytes,
        mime: resource.mime,
        sha256: resource.sha256,
        name: resource.name,
        state: resource.state,
      }
    } catch (error) {
      await this.safeRemove(filePath)
      throw this.normalize(error, signal)
    } finally {
      signal?.removeEventListener('abort', onExternal)
      this.controllers.delete(resourceId)
      this.releaseSlot()
    }
  }

  normalize(error, signal) {
    if (error instanceof MediaSpoolError) return error
    if (signal?.aborted) return new MediaSpoolError('cancel', 'Media request cancelled')
    if (isDiskError(error)) return new MediaSpoolError('disk', 'Media spool disk write failed', { code: error.code })
    return new MediaSpoolError('transport', 'Media request failed')
  }

  async fetchToDisk(canonicalPath, filePath, controller, externalSignal) {
    // Open before the fetch so the first body read is already attached to the
    // abort signal when a caller cancels immediately after dispatch.
    let handle
    try {
      handle = await this.fs.open(filePath, 'w')
    } catch (error) {
      throw new MediaSpoolError('disk', 'Media spool disk write failed', { code: error?.code })
    }
    try {
      let response
      try {
        response = await this.raceFence(
          this.fetchImpl(`${this.base}${canonicalPath}`, {
            redirect: 'error',
            signal: controller.signal,
            headers: this.token ? { 'X-KT-Host-Token': this.token } : {},
          }),
          controller,
        )
      } catch (error) {
        if (error instanceof MediaSpoolError) throw error
        if (controller.signal.aborted) throw new MediaSpoolError('cancel', 'Media request cancelled')
        if (isDiskError(error)) throw new MediaSpoolError('disk', 'Media spool disk write failed', { code: error.code })
        throw new MediaSpoolError('transport', 'Media request failed')
      }
      if (!response?.ok) throw new MediaSpoolError('transport', 'Media request failed')
      const contentLength = Number(response.headers?.get?.('content-length'))
      if (this.limits.maxBytes && Number.isFinite(contentLength) && contentLength > this.limits.maxBytes)
        throw new MediaSpoolError('oversize', 'Media is too large')
      const mime = String(response.headers?.get?.('content-type') || 'application/octet-stream')
        .split(';')[0]
        .trim()
        .toLowerCase()
      const reader = response.body?.getReader?.()
      if (!reader) throw new MediaSpoolError('transport', 'Media request failed')
      const hash = this.crypto.createHash('sha256')
      let total = 0
      for (;;) {
        const chunk = await this.readChunk(reader, controller.signal, externalSignal)
        if (chunk.done) break
        const value = chunk.value
        if (!value?.byteLength) continue
        total += value.byteLength
        if (this.limits.maxBytes && total > this.limits.maxBytes) throw new MediaSpoolError('oversize', 'Media is too large')
        hash.update(value)
        await this.writeAll(handle, value)
      }
      if (total === 0) throw new MediaSpoolError('transport', 'Media request failed')
      return { bytes: total, mime, sha256: hash.digest('hex') }
    } finally {
      await handle.close().catch(() => {})
    }
  }

  // A single FileHandle.write may write fewer bytes than the buffer (short
  // write); keep writing the remainder so the spooled file is always whole.
  async writeAll(handle, value) {
    let offset = 0
    while (offset < value.byteLength) {
      let written
      try {
        ;({ bytesWritten: written } = await handle.write(value, offset, value.byteLength - offset))
      } catch (error) {
        throw new MediaSpoolError('disk', 'Media spool disk write failed', { code: error?.code })
      }
      if (!Number.isSafeInteger(written) || written <= 0) throw new MediaSpoolError('disk', 'Media spool disk write failed')
      offset += written
    }
  }

  // Race a header fetch against the idle timer and the abort signal.
  raceFence(promise, controller) {
    const timeoutMs = this.limits.idleTimeoutMs
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (settle, value) => {
        if (settled) return
        settled = true
        if (timer) clearTimeout(timer)
        controller.signal.removeEventListener('abort', onAbort)
        settle(value)
      }
      const onAbort = () => finish(reject, new MediaSpoolError('cancel', 'Media request cancelled'))
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              finish(reject, new MediaSpoolError('timeout', 'Media request timed out'))
              controller.abort()
            }, timeoutMs)
          : null
      if (controller.signal.aborted) {
        onAbort()
        return
      }
      controller.signal.addEventListener('abort', onAbort, { once: true })
      promise.then(
        (value) => finish(resolve, value),
        (error) => finish(reject, error),
      )
    })
  }

  // Race one body read against the idle timer and the abort signal.
  readChunk(reader, signal, externalSignal) {
    const timeoutMs = this.limits.idleTimeoutMs
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (settle, value) => {
        if (settled) return
        settled = true
        if (timer) clearTimeout(timer)
        signal.removeEventListener('abort', onAbort)
        settle(value)
      }
      const onAbort = () => finish(reject, new MediaSpoolError(externalSignal?.aborted ? 'cancel' : 'cancel', 'Media request cancelled'))
      const timer =
        timeoutMs > 0 ? setTimeout(() => finish(reject, new MediaSpoolError('timeout', 'Media read idle timeout')), timeoutMs) : null
      if (signal.aborted) {
        onAbort()
        return
      }
      signal.addEventListener('abort', onAbort, { once: true })
      reader.read().then(
        (result) => finish(resolve, result),
        () => finish(reject, new MediaSpoolError('transport', 'Media request failed')),
      )
    })
  }

  resource(resourceId) {
    return this.resources.get(resourceId) || null
  }

  acquire(resourceId, lease) {
    const resource = this.resources.get(resourceId)
    if (!resource) return false
    resource.leases[lease] = (resource.leases[lease] || 0) + 1
    return true
  }

  // Drop a resource the caller no longer needs, regardless of outstanding leases.
  discard(resourceId) {
    const resource = this.resources.get(resourceId)
    if (!resource) return false
    this.resources.delete(resourceId)
    this.queueRemove(resource.filePath)
    return true
  }

  release(resourceId, lease) {
    const resource = this.resources.get(resourceId)
    if (!resource) return false
    resource.leases[lease] = Math.max(0, (resource.leases[lease] || 0) - 1)
    if (resource.leases.webview === 0 && resource.leases.editor === 0) {
      this.resources.delete(resourceId)
      this.queueRemove(resource.filePath)
    }
    return true
  }

  // Release every outstanding lease of one kind (e.g. the whole webview lease
  // surface on view dispose) while resources still held by another lease survive.
  releaseAll(lease) {
    for (const [resourceId, resource] of [...this.resources]) {
      resource.leases[lease] = 0
      if (resource.leases.webview === 0 && resource.leases.editor === 0) {
        this.resources.delete(resourceId)
        this.queueRemove(resource.filePath)
      }
    }
  }

  async isResourceLive(resourceId) {
    await Promise.all([...this.pending])
    return this.resources.has(resourceId)
  }

  async ownerRootEntries() {
    if (!this.root) return []
    await Promise.all([...this.pending])
    const names = await this.fs.readdir(this.root).catch(() => [])
    return names.filter((name) => name !== OWNER_FILE)
  }

  async dispose() {
    this.disposed = true
    // Settle every queued waiter now: none may ever reach the network.
    const error = new MediaSpoolError('disposed', 'Media spool disposed')
    for (const waiter of this.waiters.splice(0)) {
      if (waiter.onAbort) waiter.signal?.removeEventListener('abort', waiter.onAbort)
      waiter.reject(error)
    }
    for (const controller of this.controllers.values()) controller.abort()
    this.controllers.clear()
    this.resources.clear()
    const root = this.root
    this.root = null
    await Promise.all([...this.pending]).catch(() => {})
    if (root) await this.removeRoot(root)
  }
}

module.exports = { MediaSpool, MediaSpoolError, OWNER_SCHEMA, ROOT_PREFIX, DEFAULT_LIMITS }
