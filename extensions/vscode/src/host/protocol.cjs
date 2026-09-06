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
  'artifact.read',
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

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
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
        Object.keys(message).every((field) => ['type', 'requestId', 'session', 'creature', 'options'].includes(field)) &&
        (message.options === undefined || validHistoryOptions(message.options, HISTORY_PAGE_FIELDS))
      )
    case 'http.historyDetail':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        Object.keys(message).every((field) => ['type', 'requestId', 'session', 'creature', 'params'].includes(field)) &&
        validHistoryOptions(message.params, HISTORY_DETAIL_FIELDS) &&
        hasText(message.params.stream) &&
        hasText(message.params.ref) &&
        hasText(message.params.history_id)
      )
    case 'ws.send':
      return hasText(message.data) && Number.isSafeInteger(message.sendId) && message.sendId > 0
    case 'goal.execute':
      return (
        typeof message.args === 'string' &&
        Number.isSafeInteger(message.readyId) &&
        message.readyId > 0 &&
        Number.isSafeInteger(message.selectionVersion) &&
        message.selectionVersion >= 0 &&
        Object.keys(message).every((field) => ['type', 'requestId', 'args', 'readyId', 'selectionVersion'].includes(field))
      )
    case 'artifact.read':
      return (
        hasText(message.path) &&
        Number.isSafeInteger(message.readyId) &&
        message.readyId > 0 &&
        Number.isSafeInteger(message.selectionVersion) &&
        message.selectionVersion >= 0 &&
        Object.keys(message).every((field) => ['type', 'requestId', 'path', 'readyId', 'selectionVersion'].includes(field))
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
