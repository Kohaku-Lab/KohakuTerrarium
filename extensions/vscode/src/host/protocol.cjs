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
  'http.subagentConversation',
  'http.subagentList',
  'http.subagentSavedConversation',
  'http.subagentSend',
  'http.promote',
  'http.editMessage',
  'http.regenerate',
  'http.modelDirectory',
  'http.commandInventory',
  'http.switchModel',
  'http.instanceMetadata',
  'http.interrupt',
  'context.compact',
  'context.clear',
  'goal.execute',
  'platform.openLink',
  'platform.writeClipboard',
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

// Sub-agent surfaces. ``run`` identifies a persisted run; ``jobId`` a live one;
// ``name`` a sub-agent name; ``parent`` the owning creature. The Host owns each
// fixed route, so a live read and a saved read are separate messages and the
// saved surface has no send counterpart at all.
const SUBAGENT_LIVE_FIELDS = ['jobId', 'name', 'run']
const SUBAGENT_LIST_FIELDS = ['parent', 'jobId', 'name']
const SUBAGENT_SAVED_FIELDS = ['parent', 'jobId', 'name', 'run']

// Branch-mutation surfaces. The Host owns the fixed route and canonical body
// field names, so a compromised webview can smuggle neither a URL/method/header
// nor an arbitrary field. The nullable shared options are turn_index /
// user_position / branch_view; a persisted locator is the durable turn identity
// and the correlation id is a DTO echo only, distinct from the transport id.
const BRANCH_OPTION_FIELDS = ['turnIndex', 'branchView', 'correlationId', 'locator']
const REGENERATE_FIELDS = ['type', 'requestId', 'session', 'creature', 'readyId', ...BRANCH_OPTION_FIELDS]
const EDIT_MESSAGE_FIELDS = [
  'type',
  'requestId',
  'session',
  'creature',
  'readyId',
  'msgIdx',
  'content',
  'userPosition',
  ...BRANCH_OPTION_FIELDS,
]

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

function validSubagentField(key, value) {
  if (value == null) return true
  if (key === 'run') return (typeof value === 'string' && value.length > 0) || Number.isSafeInteger(value)
  return typeof value === 'string' && value.length > 0
}

function validSubagentOptions(value, fields) {
  if (value === undefined) return true
  if (!isPlainObject(value)) return false
  const keys = Object.keys(value)
  if (!keys.every((key) => fields.includes(key))) return false
  return keys.every((key) => validSubagentField(key, value[key]))
}

// ``branch_view`` is a turn->branch map of non-negative integers; a non-numeric
// key or value is refused so the body stays the canonical backend shape.
function validBranchView(value) {
  if (value == null) return true
  if (!isPlainObject(value)) return false
  return Object.entries(value).every(([key, branch]) => /^\d+$/.test(key) && Number.isSafeInteger(branch) && branch >= 0)
}

// The durable locator is the persisted turn identity: every id is a positive
// integer and no other key may ride along.
function validLocator(value) {
  if (value == null) return true
  if (!isPlainObject(value) || !hasOnlyFields(value, ['eventId', 'turnIndex', 'branchId'])) return false
  return validPositiveInt(value.eventId) && validPositiveInt(value.turnIndex) && validPositiveInt(value.branchId)
}

function validOptionalCount(value) {
  return value == null || (Number.isSafeInteger(value) && value >= 0)
}

function validCorrelation(value) {
  return value == null || hasText(value)
}

// Edit content is text or an already-serialized parts array; a live File/Blob
// (non-plain-prototype object) is refused so it can never cross the wire.
function validContent(value) {
  if (typeof value === 'string') return true
  if (!Array.isArray(value)) return false
  return value.every((part) => {
    if (!isPlainObject(part)) return false
    const proto = Object.getPrototypeOf(part)
    return proto === Object.prototype || proto === null
  })
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
    case 'http.subagentConversation':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'options']) &&
        validSubagentOptions(message.options, SUBAGENT_LIVE_FIELDS)
      )
    case 'http.subagentList':
      return (
        hasText(message.session) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'options']) &&
        validSubagentOptions(message.options, SUBAGENT_LIST_FIELDS)
      )
    case 'http.subagentSavedConversation':
      return (
        hasText(message.session) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'options']) &&
        validSubagentOptions(message.options, SUBAGENT_SAVED_FIELDS)
      )
    case 'http.subagentSend':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasText(message.name) &&
        typeof message.content === 'string' &&
        message.content.length > 0 &&
        (message.jobId === undefined || hasText(message.jobId)) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'name', 'content', 'jobId'])
      )
    case 'http.promote':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasText(message.jobId) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'jobId'])
      )
    case 'http.regenerate':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        validPositiveInt(message.readyId) &&
        validOptionalCount(message.turnIndex) &&
        validBranchView(message.branchView) &&
        validCorrelation(message.correlationId) &&
        validLocator(message.locator) &&
        hasOnlyFields(message, REGENERATE_FIELDS)
      )
    case 'http.editMessage':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        validPositiveInt(message.readyId) &&
        Number.isSafeInteger(message.msgIdx) &&
        message.msgIdx >= 0 &&
        validContent(message.content) &&
        validOptionalCount(message.turnIndex) &&
        validOptionalCount(message.userPosition) &&
        validBranchView(message.branchView) &&
        validCorrelation(message.correlationId) &&
        validLocator(message.locator) &&
        hasOnlyFields(message, EDIT_MESSAGE_FIELDS)
      )
    // Model/slash + instance-metadata surfaces: each fixed host route carries the
    // stable target identities, the canonical selector and the ready-ownership epoch.
    case 'http.modelDirectory':
      // Host-global read: only the ready epoch fences it, so no target identity
      // may be smuggled into the envelope.
      return validPositiveInt(message.readyId) && hasOnlyFields(message, ['type', 'requestId', 'readyId'])
    case 'http.commandInventory':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        validPositiveInt(message.readyId) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'readyId'])
      )
    case 'http.switchModel':
      return (
        hasText(message.session) &&
        hasText(message.creature) &&
        hasText(message.model) &&
        validPositiveInt(message.readyId) &&
        hasOnlyFields(message, ['type', 'requestId', 'session', 'creature', 'model', 'readyId'])
      )
    case 'http.instanceMetadata':
      return (
        hasText(message.session) && validPositiveInt(message.readyId) && hasOnlyFields(message, ['type', 'requestId', 'session', 'readyId'])
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
    case 'platform.writeClipboard':
      return hasOnlyFields(message, ['type', 'requestId', 'text']) && typeof message.text === 'string'
    case 'platform.openLink':
      return (
        hasText(message.target) && validPositiveInt(message.readyId) && hasOnlyFields(message, ['type', 'requestId', 'target', 'readyId'])
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
