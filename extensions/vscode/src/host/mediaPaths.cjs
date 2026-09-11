// The one route the Host will fetch for a media reference: a canonical
// /api/sessions/{namespace}/artifacts/{segments} route, or the fixed
// /api/files/raw?path= route derived from a local path. Anything else yields null
// (the Host refuses it). A webview never names a fetch target itself.
//
// The backend is the authority on which file a route resolves to. The ASGI
// server percent-decodes the request path once, and the artifact handler
// ``unquote``s the filepath a second time, so a filepath survives two decode
// levels. A literal ``%`` (the backend emits it as ``%25``) therefore stays
// literal instead of being rejected — the Host mirrors the backend's decode to
// decide traversal, then preserves the caller's exact valid percent-encoding so
// a route the backend emitted is re-decoded by the backend rather than
// re-encoded by the Host (``%2528`` must not collapse onto ``(``).
const DEFAULT_LIMITS = Object.freeze({
  maxPathLength: 2048,
})

const ROUTE_PREFIX = '/api/sessions/'
const ARTIFACT_MARKER = '/artifacts/'
const RAW_ROUTE_PREFIX = '/api/files/raw?path='
// A reference that names the backend's own HTTP surface is a ROUTE, never a
// local filesystem path, so a refused route can never be reinterpreted as a raw
// file fetch of its own text.
const API_PREFIX = '/api/'
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/
// The fixed raw-file route. The query carries one percent-encoded local path with
// no further separators, so a webview can never smuggle a second parameter.
const RAW_ROUTE = /^\/api\/files\/raw\?path=([^&#\s]+)$/
const PERCENT_RUN = /(?:%[0-9A-Fa-f]{2})+/g
const HEX = /%[0-9A-Fa-f]{2}/g
const UTF8 = new TextDecoder('utf-8', { fatal: false })

// Mirror ``urllib.parse.unquote`` exactly: decode each ``%XX`` byte run as UTF-8
// (replacing malformed sequences) and leave a truncated/invalid ``%`` verbatim,
// so this never throws the way ``decodeURIComponent`` does on a literal percent.
function unquoteOnce(value) {
  return value.replace(PERCENT_RUN, (run) => {
    const bytes = run.match(HEX).map((hex) => parseInt(hex.slice(1), 16))
    return UTF8.decode(Uint8Array.from(bytes))
  })
}

// A segment the backend could resolve to a dot, an empty component, or a new
// path separator (``/`` or ``\``) — the shapes that let a route walk to a
// different artifact. A literal ``%`` or ``#`` in the decoded name is a normal
// filename character, not a separator.
function unsafeSegment(decoded) {
  if (!decoded || decoded === '.' || decoded === '..') return true
  return decoded.includes('/') || decoded.includes('\\') || CONTROL_PATTERN.test(decoded)
}

function canonicalArtifactPath(raw, maxPathLength = DEFAULT_LIMITS.maxPathLength) {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > maxPathLength) return null
  if (!raw.startsWith(ROUTE_PREFIX)) return null
  if (raw.includes('?') || raw.includes('#') || raw.includes('\\') || CONTROL_PATTERN.test(raw)) return null
  const rest = raw.slice(ROUTE_PREFIX.length)
  const namespaceEnd = rest.indexOf('/')
  if (namespaceEnd <= 0 || !rest.startsWith(ARTIFACT_MARKER, namespaceEnd)) return null
  const namespace = rest.slice(0, namespaceEnd)
  const segments = rest.slice(namespaceEnd + ARTIFACT_MARKER.length).split('/')
  if (segments.some((segment) => segment.length === 0)) return null
  // The ASGI server decodes the namespace once (the handler never re-unquotes it).
  if (unsafeSegment(unquoteOnce(namespace))) return null
  // The handler unquotes the filepath a second time. Reject any segment whose
  // fully-decoded form is a dot/empty/separator, so a dot segment the URL
  // normalizer would collapse, or a hidden separator, can never redirect the
  // route. Everything else is a valid backend name and is preserved as sent.
  for (const segment of segments) {
    if (unsafeSegment(unquoteOnce(unquoteOnce(segment)))) return null
  }
  return raw
}

// A local raw file path (what a ``file://`` media reference names). Only an
// absolute path is accepted; ``.``/``..`` are delegated to the backend's own
// ``Path.resolve`` containment rather than re-authorized here, so the fixed raw
// route reaches the same file the reference names without the Host inventing a
// stricter filesystem policy.
function rawFilePath(value, maxPathLength = DEFAULT_LIMITS.maxPathLength) {
  if (typeof value !== 'string' || value.length === 0 || value.length > maxPathLength) return null
  if (CONTROL_PATTERN.test(value) || value.includes('://')) return null
  if (!value.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(value)) return null
  return value
}

// Re-canonicalize an already-derived raw route so a later validation pass is
// idempotent (``mediaFetchTarget(route) === route``).
function canonicalRawRoute(value, maxPathLength = DEFAULT_LIMITS.maxPathLength) {
  const match = typeof value === 'string' ? RAW_ROUTE.exec(value) : null
  if (!match) return null
  const raw = rawFilePath(unquoteOnce(match[1]), maxPathLength)
  return raw ? `${RAW_ROUTE_PREFIX}${encodeURIComponent(raw)}` : null
}

// The one route the Host will fetch for a media reference: a canonical artifact
// route, or the fixed raw-file route derived from a local path. Anything else is
// not displayable and yields null (the Host refuses it).
function mediaFetchTarget(value, maxPathLength = DEFAULT_LIMITS.maxPathLength) {
  const artifact = canonicalArtifactPath(value, maxPathLength)
  if (artifact) return artifact
  const route = canonicalRawRoute(value, maxPathLength)
  if (route) return route
  if (typeof value === 'string' && value.startsWith(API_PREFIX)) return null
  const raw = rawFilePath(value, maxPathLength)
  if (raw) return `${RAW_ROUTE_PREFIX}${encodeURIComponent(raw)}`
  return null
}

module.exports = {
  canonicalArtifactPath,
  rawFilePath,
  canonicalRawRoute,
  mediaFetchTarget,
}
