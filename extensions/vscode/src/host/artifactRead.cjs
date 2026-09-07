// Loopback-only artifact GET: authenticated, redirect-hostile, size- and signature-checked,
// streamed into a data URL.
const { canonicalArtifactPath } = require('./artifactRegistry.cjs')

const DEFAULT_LIMITS = Object.freeze({
  maxBytes: 8 * 1024 * 1024,
  timeoutMs: 10_000,
  maxConcurrent: 4,
})

const ALLOWED_MIME = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])

function httpBaseFromWebSocket(base) {
  return String(base).replace(/^ws:/, 'http:')
}

function signatureMatches(mime, head) {
  const bytes = (value) => head[value]
  if (mime === 'image/png') {
    const mark = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
    return head.length >= mark.length && mark.every((byte, index) => bytes(index) === byte)
  }
  if (mime === 'image/jpeg') return head.length >= 3 && bytes(0) === 0xff && bytes(1) === 0xd8 && bytes(2) === 0xff
  if (mime === 'image/gif') {
    const mark = String.fromCharCode(...head.subarray(0, 6))
    return mark === 'GIF87a' || mark === 'GIF89a'
  }
  if (mime === 'image/webp') {
    return (
      head.length >= 12 && String.fromCharCode(...head.subarray(0, 4)) === 'RIFF' && String.fromCharCode(...head.subarray(8, 12)) === 'WEBP'
    )
  }
  return false
}

function mimeOf(header) {
  return String(header ?? '')
    .split(';')[0]
    .trim()
    .toLowerCase()
}

class ArtifactReader {
  constructor({ base, token = '', fetchImpl = fetch, limits = {}, maxBytes, timeoutMs, maxConcurrent } = {}) {
    this.base = base
    this.token = token
    this.fetchImpl = fetchImpl
    this.limits = { ...DEFAULT_LIMITS, ...limits }
    if (maxBytes !== undefined) this.limits.maxBytes = maxBytes
    if (timeoutMs !== undefined) this.limits.timeoutMs = timeoutMs
    if (maxConcurrent !== undefined) this.limits.maxConcurrent = maxConcurrent
    this.inFlight = 0
  }

  async read(canonicalPath, { signal } = {}) {
    if (signal?.aborted) throw Error('Artifact read cancelled')
    const canonical = canonicalArtifactPath(canonicalPath)
    if (!canonical || canonical !== canonicalPath) throw Error('Unknown artifact reference')
    if (this.inFlight >= this.limits.maxConcurrent) throw Error('Artifact read limit reached')
    this.inFlight++
    const controller = new AbortController()
    let timedOut = false
    let externallyCancelled = false
    const onExternal = () => {
      externallyCancelled = true
      controller.abort()
    }
    signal?.addEventListener('abort', onExternal, { once: true })
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.limits.timeoutMs)
    const aborted = new Promise((_, reject) => {
      controller.signal.addEventListener(
        'abort',
        () =>
          reject(Error(timedOut ? 'Artifact read timed out' : externallyCancelled ? 'Artifact read cancelled' : 'Artifact read aborted')),
        { once: true },
      )
    })
    aborted.catch(() => {})
    const guard = (promise) => Promise.race([promise, aborted])
    try {
      return await this.perform(canonical, controller, guard)
    } finally {
      // Abort the response body on every exit: success, rejected type, headers, or error.
      controller.abort()
      clearTimeout(timeout)
      signal?.removeEventListener('abort', onExternal)
      this.inFlight--
    }
  }

  async perform(canonicalPath, controller, guard) {
    let response
    try {
      response = await guard(
        this.fetchImpl(`${this.base}${canonicalPath}`, {
          redirect: 'error',
          signal: controller.signal,
          headers: this.token ? { 'X-KT-Host-Token': this.token } : {},
        }),
      )
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('Artifact read')) throw error
      throw Error('Artifact request failed')
    }
    if (!response?.ok) throw Error('Artifact request failed')
    const mime = mimeOf(response.headers?.get?.('content-type'))
    if (!ALLOWED_MIME.has(mime)) throw Error('Artifact type is not supported')
    const contentLength = Number(response.headers?.get?.('content-length'))
    if (Number.isFinite(contentLength) && contentLength > this.limits.maxBytes) throw Error('Artifact is too large')
    const bytes = await this.drain(response.body, controller, guard)
    if (!signatureMatches(mime, bytes.subarray(0, 12))) throw Error('Artifact content is not supported')
    return `data:${mime};base64,${Buffer.from(bytes).toString('base64')}`
  }

  async drain(body, controller, guard) {
    const reader = body?.getReader?.()
    if (!reader) throw Error('Artifact request failed')
    const chunks = []
    let total = 0
    for (;;) {
      let done
      let chunk
      try {
        ;({ done, value: chunk } = await guard(reader.read()))
      } catch (error) {
        if (error instanceof Error && error.message.startsWith('Artifact read')) throw error
        throw Error('Artifact request failed')
      }
      if (done) break
      if (!chunk?.byteLength) continue
      total += chunk.byteLength
      if (total > this.limits.maxBytes) throw Error('Artifact is too large')
      chunks.push(chunk)
    }
    if (total === 0) throw Error('Artifact content is not supported')
    const bytes = new Uint8Array(total)
    let offset = 0
    for (const chunk of chunks) {
      bytes.set(chunk, offset)
      offset += chunk.byteLength
    }
    return bytes
  }
}

module.exports = { ArtifactReader, httpBaseFromWebSocket }
