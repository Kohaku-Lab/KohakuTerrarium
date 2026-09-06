// Canonical /api/sessions/{namespace}/artifacts/{segments} refs the Host may fetch.
const DEFAULT_LIMITS = Object.freeze({
  maxRefs: 1024,
  maxScanBytes: 2 * 1024 * 1024,
  maxNodes: 4096,
  maxDepth: 32,
  maxPathLength: 2048,
})

const ROUTE_PREFIX = '/api/sessions/'
const ARTIFACT_MARKER = '/artifacts/'
// Characters that end an embedded URL; ')' is handled with paren balancing.
const URL_TERMINATORS = new Set([' ', '\t', '\n', '\r', '"', "'", '`', '<', '>', '\\', ')'])
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/

// The backend unquotes a filepath twice; every decoded level must stay traversal-free.
function decodeTwice(value) {
  return decodeURIComponent(decodeURIComponent(value))
}

function hasSeparatorsOrControls(value) {
  if (value.includes('/') || value.includes('\\') || CONTROL_PATTERN.test(value)) return true
  return false
}

function canonicalArtifactPath(raw, maxPathLength = DEFAULT_LIMITS.maxPathLength) {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > maxPathLength) return null
  if (!raw.startsWith(ROUTE_PREFIX)) return null
  if (raw.includes('?') || raw.includes('#') || raw.includes('\\') || CONTROL_PATTERN.test(raw)) return null
  const rest = raw.slice(ROUTE_PREFIX.length)
  const namespaceEnd = rest.indexOf('/')
  if (namespaceEnd <= 0 || !rest.startsWith(ARTIFACT_MARKER, namespaceEnd)) return null
  const segments = rest.slice(namespaceEnd + ARTIFACT_MARKER.length).split('/')
  if (segments.some((segment) => segment.length === 0)) return null
  let namespace
  let decoded
  try {
    namespace = decodeTwice(rest.slice(0, namespaceEnd))
    decoded = segments.map(decodeTwice)
  } catch {
    return null
  }
  if (!namespace || namespace === '.' || namespace === '..' || hasSeparatorsOrControls(namespace)) return null
  if (decoded.some((segment) => !segment || segment === '.' || segment === '..' || hasSeparatorsOrControls(segment))) return null
  // A residual percent means the backend has a further decode level that could still
  // expose separators or dot segments past every level checked here.
  if (namespace.includes('%') || decoded.some((segment) => segment.includes('%'))) return null
  if (`${namespace}/${decoded.join('/')}`.includes('://')) return null
  return `${ROUTE_PREFIX}${encodeURIComponent(namespace)}/artifacts/${decoded.map(encodeURIComponent).join('/')}`
}

// The namespace of an already-canonical ref (encoded exactly once by canonicalArtifactPath).
function artifactNamespaceOf(canonical) {
  if (typeof canonical !== 'string' || !canonical.startsWith(ROUTE_PREFIX)) return null
  const rest = canonical.slice(ROUTE_PREFIX.length)
  const namespaceEnd = rest.indexOf('/')
  if (namespaceEnd <= 0 || !rest.startsWith(ARTIFACT_MARKER, namespaceEnd)) return null
  try {
    return decodeURIComponent(rest.slice(0, namespaceEnd))
  } catch {
    return null
  }
}

// A match preceded by an authority (scheme://host) is an absolute URL and is not collected.
function precededByAuthority(text, index) {
  const limit = Math.max(0, index - 1024)
  let start = index
  while (start > limit && !URL_TERMINATORS.has(text[start - 1])) start--
  return text.slice(start, index).includes('://')
}

function extractArtifactUrls(text, visit) {
  let index = text.indexOf(ROUTE_PREFIX)
  while (index !== -1) {
    if (!precededByAuthority(text, index)) {
      let end = index
      let depth = 0
      while (end < text.length) {
        const char = text[end]
        if (char === '(') depth++
        else if (char === ')') {
          if (depth === 0) break
          depth--
        } else if (URL_TERMINATORS.has(char)) break
        end++
      }
      visit(text.slice(index, end))
    }
    index = text.indexOf(ROUTE_PREFIX, index + ROUTE_PREFIX.length)
  }
}

class ArtifactRegistry {
  constructor(limits = {}) {
    this.limits = { ...DEFAULT_LIMITS, ...limits }
    this.refs = new Set()
  }

  admit(path) {
    const canonical = canonicalArtifactPath(path, this.limits.maxPathLength)
    if (!canonical || this.refs.size >= this.limits.maxRefs) return false
    this.refs.add(canonical)
    return true
  }

  admitCanonical(canonical) {
    if (this.refs.size >= this.limits.maxRefs) return false
    this.refs.add(canonical)
    return true
  }

  allowsCanonical(canonical) {
    return this.refs.has(canonical)
  }

  allowed(path) {
    const canonical = canonicalArtifactPath(path, this.limits.maxPathLength)
    return canonical !== null && this.refs.has(canonical)
  }

  observe(value) {
    return this.walk(value, 0, { nodes: 0, bytes: 0 })
  }

  // Frames are opaque; only bounded parsed JSON objects are scanned.
  observeFrameText(text) {
    if (typeof text !== 'string' || text.length > this.limits.maxScanBytes) return 0
    let frame
    try {
      frame = JSON.parse(text)
    } catch {
      return 0
    }
    if (!frame || typeof frame !== 'object') return 0
    return this.walk(frame, 0, { nodes: 0, bytes: 0 })
  }

  walk(node, depth, seen) {
    if (seen.stopped || this.refs.size >= this.limits.maxRefs) return 0
    if (++seen.nodes > this.limits.maxNodes || depth > this.limits.maxDepth) {
      seen.stopped = true
      return 0
    }
    if (typeof node === 'string') {
      seen.bytes += node.length
      if (seen.bytes > this.limits.maxScanBytes) {
        seen.stopped = true
        return 0
      }
      let admitted = 0
      extractArtifactUrls(node, (candidate) => {
        if (this.admit(candidate)) admitted++
      })
      return admitted
    }
    if (Array.isArray(node)) {
      let admitted = 0
      for (const item of node) {
        if (seen.stopped) break
        admitted += this.walk(item, depth + 1, seen)
      }
      return admitted
    }
    if (node && typeof node === 'object') {
      let admitted = 0
      for (const key of Object.keys(node)) {
        if (seen.stopped) break
        admitted += this.walk(node[key], depth + 1, seen)
      }
      return admitted
    }
    return 0
  }

  invalidate() {
    this.refs.clear()
  }
}

module.exports = { ArtifactRegistry, artifactNamespaceOf, canonicalArtifactPath }
