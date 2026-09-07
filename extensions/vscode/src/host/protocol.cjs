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
