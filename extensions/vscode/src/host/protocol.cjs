const ALLOWED = new Set([
  'ready',
  'session.list',
  'session.create',
  'session.resume',
  'session.stop',
  'session.reconcile',
  'session.clearSelection',
  'session.select',
  'http.history',
  'http.historyPage',
  'http.historyDetail',
  'http.interrupt',
  'context.compact',
  'context.clear',
  'goal.execute',
  'media.prepare',
  'media.release',
  'media.cancel',
  'media.open',
  'media.save',
  'ws.open',
  'ws.send',
  'ws.close',
])
const FORBIDDEN_FIELDS = [
  'token',
  'endpoint',
  'configPath',
  'config_path',
  'pwd',
  'workspacePath',
  'workspace_path',
  'configRef',
  'configReference',
  'config_reference',
]

function hasText(value) {
  return typeof value === 'string' && value.length > 0
}

// Paged/detail history option surfaces. The Host owns the fixed route and
// rejects any option key or value shape it does not understand, so a
// compromised webview cannot smuggle query params or arbitrary fields.
const HISTORY_PAGE_FIELDS = ['limit', 'before', 'after', 'history_id', 'stream']
const HISTORY_DETAIL_FIELDS = ['stream', 'ref', 'history_id']
const HISTORY_STREAMS = new Set(['events', 'snapshot', 'channel'])

// Media envelopes are the exact closed surfaces the Webview may send. ``path`` may
// be a canonical artifact route OR a raw file path: the Host's canonical route
// resolution is the only gate that may block a raw path, so the envelope itself
// only refuses an absolute URL that would smuggle an arbitrary fetch target.
// Leases are a fixed set.
const MEDIA_PREPARE_FIELDS = ['type', 'requestId', 'path', 'name', 'readyId', 'selectionVersion']
const MEDIA_RESOURCE_FIELDS = ['type', 'requestId', 'resourceId']
const MEDIA_RELEASE_FIELDS = ['type', 'requestId', 'resourceId', 'lease']
const MEDIA_CANCEL_FIELDS = ['type', 'requestId', 'resourceId', 'prepareRequestId']
const MEDIA_LEASES = new Set(['webview', 'editor'])

// An absolute URL names its own fetch target, so the media envelope refuses one.
// The scheme is anchored at the very start (and must be a real 2+ char scheme, so
// a Windows drive letter like ``C:/x`` is not read as a scheme); scanning for
// ``://`` anywhere would misclassify a raw path whose segments merely contain it.
const ABSOLUTE_URL = /^[A-Za-z][A-Za-z0-9+.-]+:\/\//

function isAbsoluteUrl(value) {
  return ABSOLUTE_URL.test(value)
}

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function hasOnlyFields(message, fields) {
  return Object.keys(message).every((field) => fields.includes(field))
}

function validPositiveInt(value) {
  return Number.isSafeInteger(value) && value > 0
}

function validHistoryField(key, value) {
  if (value == null) return true
  if (key === 'limit') return Number.isSafeInteger(value) && value > 0
  if (key === 'stream') return typeof value === 'string' && HISTORY_STREAMS.has(value)
  return typeof value === 'string' && value.length > 0
}

function validHistoryOptions(value, fields) {
  if (!isPlainObject(value)) return false
  const keys = Object.keys(value)
  if (!keys.every((key) => fields.includes(key))) return false
  if (!keys.every((key) => validHistoryField(key, value[key]))) return false
  if (value.before != null && value.after != null) return false
  return true
}

function allowedMessage(message) {
  if (!message || typeof message !== 'object' || Array.isArray(message)) return false
  if (!ALLOWED.has(message.type) || Object.hasOwn(message, 'id')) return false
  const socketMessage = message.type.startsWith('ws.')
  const identifier = socketMessage ? message.socketId : message.requestId
  if (!Number.isSafeInteger(identifier) || identifier < 1) return false
  if (socketMessage ? Object.hasOwn(message, 'requestId') : Object.hasOwn(message, 'socketId')) return false
  if (FORBIDDEN_FIELDS.some((field) => Object.hasOwn(message, field))) return false

  switch (message.type) {
    case 'session.resume':
      return hasText(message.savedName)
    case 'session.select':
    case 'session.stop':
      return hasText(message.session) && hasText(message.creatureId)
    case 'http.history':
    case 'http.interrupt':
      return hasText(message.session) && hasText(message.creature)
    case 'http.historyPage':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'options']) &&
        (message.options === undefined || validHistoryOptions(message.options, HISTORY_PAGE_FIELDS))
      )
    case 'http.historyDetail':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'params']) &&
        validHistoryOptions(message.params, HISTORY_DETAIL_FIELDS) &&
        hasText(message.params.stream) &&
        hasText(message.params.ref) &&
        hasText(message.params.history_id)
      )
    case 'media.prepare':
      return (
        hasText(message.path) &&
        // An absolute URL is not a raw path; refuse it here so a compromised webview
        // cannot name its own fetch target. A canonical route or raw file path passes.
        !isAbsoluteUrl(message.path) &&
        validPositiveInt(message.readyId) &&
        Number.isSafeInteger(message.selectionVersion) &&
        message.selectionVersion >= 0 &&
        (message.name === undefined || hasText(message.name)) &&
        hasOnlyFields(message, MEDIA_PREPARE_FIELDS)
      )
    case 'media.open':
    case 'media.save':
      return hasText(message.resourceId) && hasOnlyFields(message, MEDIA_RESOURCE_FIELDS)
    case 'media.release':
      return (
        hasText(message.resourceId) &&
        (message.lease === undefined || MEDIA_LEASES.has(message.lease)) &&
        hasOnlyFields(message, MEDIA_RELEASE_FIELDS)
      )
    case 'media.cancel':
      return (
        (hasText(message.resourceId) || validPositiveInt(message.prepareRequestId)) &&
        (message.prepareRequestId === undefined || validPositiveInt(message.prepareRequestId)) &&
        hasOnlyFields(message, MEDIA_CANCEL_FIELDS)
      )
    case 'ws.send':
      return hasText(message.data) && Number.isSafeInteger(message.sendId) && message.sendId > 0
    case 'goal.execute':
      return (
        typeof message.args === 'string' &&
        validPositiveInt(message.readyId) &&
        Number.isSafeInteger(message.selectionVersion) &&
        message.selectionVersion >= 0 &&
        hasOnlyFields(message, ['type', 'requestId', 'args', 'readyId', 'selectionVersion'])
      )
    case 'context.compact':
    case 'context.clear':
      return Object.keys(message).every((field) => field === 'type' || field === 'requestId')
    default:
      return true
  }
}

function validateEndpoint(value) {
  let url
  try {
    url = new URL(value)
  } catch {
    throw Error('Invalid endpoint')
  }
  if (
    url.protocol !== 'http:' ||
    !['127.0.0.1', '[::1]'].includes(url.hostname) ||
    !url.port ||
    url.pathname !== '/' ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw Error('Endpoint must be an explicit-port loopback URL')
  }
  return url.origin
}

module.exports = { allowedMessage, validateEndpoint }
