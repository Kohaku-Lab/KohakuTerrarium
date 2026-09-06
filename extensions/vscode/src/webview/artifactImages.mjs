function artifactPath(value) {
  if (typeof value !== 'string' || value.length > 2048 || !/^\/api\/sessions\/[^/]+\/artifacts\/.+/.test(value)) return false
  try {
    let decoded = value
    for (let count = 0; count < 3; count++) {
      if (/[\\?#\u0000-\u001f\u007f]/.test(decoded) || decoded.split('/').some((part) => part === '.' || part === '..')) return false
      decoded = decodeURIComponent(decoded)
    }
    return !/[%\\?#\u0000-\u001f\u007f]/.test(decoded) && !decoded.split('/').some((part) => part === '.' || part === '..')
  } catch {
    return false
  }
}

export function createArtifactLoader({ request, getFence, maxConcurrent = 3, maxEntries = 64, maxChars = 16 * 1024 * 1024 }) {
  const entries = new Map()
  const waiting = []
  let epoch = 0
  let active = 0
  let disposed = false
  let chars = 0
  const keyOf = (fence) => JSON.stringify(fence)
  const trim = () => {
    for (const [key, entry] of entries) {
      if (entries.size <= maxEntries && chars <= maxChars) break
      if (entry.size === undefined) continue
      entries.delete(key)
      chars -= entry.size
    }
  }
  function pump() {
    while (!disposed && active < maxConcurrent && waiting.length) {
      const job = waiting.shift()
      if (job.epoch !== epoch) continue
      active++
      Promise.resolve()
        .then(() => request('artifact.read', { path: job.path, ...job.fence }))
        .then((result) => {
          if (disposed || job.epoch !== epoch || job.owner !== keyOf(getFence())) throw Error('Artifact ownership changed')
          if (
            typeof result?.dataUrl !== 'string' ||
            result.dataUrl.length > 12 * 1024 * 1024 ||
            !/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]*={0,2}$/.test(result.dataUrl)
          )
            throw Error('Invalid artifact image')
          job.entry.size = result.dataUrl.length
          chars += job.entry.size
          job.resolve(result.dataUrl)
          trim()
        })
        .catch((error) => {
          if (entries.get(job.key) === job.entry) entries.delete(job.key)
          job.reject(error)
        })
        .finally(() => {
          active--
          pump()
        })
    }
  }
  return {
    load(path) {
      if (disposed || !artifactPath(path)) return Promise.reject(Error('Artifact unavailable or disposed'))
      const fence = getFence()
      if (!fence) return Promise.reject(Error('Artifact ownership unavailable'))
      const owner = keyOf(fence)
      const key = `${owner}:${path}`
      if (entries.has(key)) return entries.get(key).promise
      trim()
      if (waiting.length >= 64) return Promise.reject(Error('Artifact request limit reached'))
      const entry = {}
      entry.promise = new Promise((resolve, reject) => {
        waiting.push({ path, fence, owner, key, entry, resolve, reject, epoch })
        entry.reject = reject
      })
      entries.set(key, entry)
      pump()
      return entry.promise
    },
    reset() {
      epoch++
      for (const entry of entries.values()) if (entry.size === undefined) entry.reject(Error('Artifact ownership changed'))
      entries.clear()
      waiting.length = 0
      chars = 0
    },
    dispose() {
      disposed = true
      this.reset()
    },
  }
}

export function observeArtifactImages(root, loader) {
  const tracked = new WeakMap()
  let disposed = false
  function scan() {
    if (disposed) return
    for (const image of root.querySelectorAll('img')) {
      const path = image.getAttribute('src')
      if (!path?.startsWith('/api/sessions/')) continue
      const previous = tracked.get(image)
      if (previous?.path === path && previous.loading) continue
      previous?.status.remove()
      image.removeAttribute('src')
      const status = root.ownerDocument.createElement('span')
      status.setAttribute('role', 'status')
      status.className = 'artifact-image-status'
      status.textContent = 'Loading image…'
      image.after(status)
      const entry = { path, status, loading: true }
      tracked.set(image, entry)
      loader
        .load(path)
        .then((dataUrl) => {
          if (disposed || !root.contains(image) || tracked.get(image) !== entry) return
          entry.loading = false
          image.setAttribute('src', dataUrl)
          status.remove()
        })
        .catch(() => {
          if (disposed || !root.contains(image) || tracked.get(image) !== entry) return
          entry.loading = false
          status.textContent = 'Image unavailable '
          const retry = root.ownerDocument.createElement('button')
          retry.type = 'button'
          retry.setAttribute('aria-label', 'Retry image')
          retry.textContent = 'Retry'
          retry.addEventListener('click', () => {
            if (!disposed) {
              image.setAttribute('src', path)
              scan()
            }
          })
          status.append(retry)
          image.setAttribute('alt', image.getAttribute('alt') || 'Image unavailable')
        })
    }
  }
  const Observer = root.ownerDocument.defaultView.MutationObserver
  const observer = new Observer(scan)
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] })
  scan()
  return () => {
    disposed = true
    observer.disconnect()
  }
}
