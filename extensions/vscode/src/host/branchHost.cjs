const { allowedMessage } = require('./protocol.cjs')

// Fixed branch-mutation transport (regenerate / edit). The Host owns the route,
// method and body; the webview names only a target, a persisted locator and the
// nullable shared options. Admission is serialized with selection/ready, but the
// queue is released before the long POST settles, so a whole rerun turn never
// blocks select/refresh/interrupt.
const BRANCH_TYPES = new Set(['http.editMessage', 'http.regenerate'])

function branchOptions(message) {
  const options = {}
  if (message.turnIndex != null) options.turnIndex = message.turnIndex
  if (message.userPosition != null) options.userPosition = message.userPosition
  if (message.branchView !== undefined) options.branchView = message.branchView
  if (message.correlationId !== undefined) options.correlationId = message.correlationId
  if (message.locator !== undefined) options.locator = message.locator
  return options
}

// Tag a thrown branch error with the transport phase the shared store guard
// reads: ``mayHaveRun`` is true only once the POST was dispatched, and
// ``superseded`` marks an ownership/lifecycle supersession (never a backend
// cancel). No backend detail, URL, token or stack crosses over.
function tagBranchError(error, { mayHaveRun, superseded }) {
  if (error && typeof error === 'object') {
    error.mayHaveRun = mayHaveRun
    if (superseded) error.superseded = true
  }
  return error
}

async function runBranch(host, message, start) {
  const readyId = host.runtimeEpoch
  let selected
  try {
    if (host.disposed) throw Error('Runtime disposed')
    if (message.readyId !== readyId) throw Error('Ready ownership changed')
    selected = host.requireSelection(message)
  } catch (error) {
    throw tagBranchError(error, { mayHaveRun: false, superseded: false })
  }

  const intent = host.selectionIntentVersion
  const controller = new AbortController()
  host.branchControllers.add(controller)
  let dispatched = false
  try {
    let started
    try {
      started = await host.enqueueSelectionOperation(async () => {
        if (host.disposed || controller.signal.aborted || host.state.selection !== selected || intent !== host.selectionIntentVersion) {
          throw Error('Selected Creature ownership changed')
        }
        dispatched = true
        // Return a holder (not the promise) so the admission queue resolves at
        // once and the long POST never holds the selection tail.
        return { promise: start(selected, controller.signal) }
      })
    } catch (error) {
      throw tagBranchError(error, { mayHaveRun: dispatched, superseded: !dispatched })
    }

    let data
    try {
      data = await started.promise
    } catch (error) {
      // An aborted wait is a supersession (ready reset / selection change /
      // dispose), not a backend failure; a real transport error is not.
      throw tagBranchError(error, { mayHaveRun: dispatched, superseded: controller.signal.aborted })
    }
    if (!host.ownsSelectedRead(selected, readyId, intent)) {
      throw tagBranchError(Error('Selected Creature ownership changed'), { mayHaveRun: true, superseded: true })
    }
    return data
  } finally {
    host.branchControllers.delete(controller)
  }
}

function dispatchBranch(host, message) {
  if (!allowedMessage(message)) throw Error('Invalid branch mutation request')
  const start =
    message.type === 'http.regenerate'
      ? (selected, signal) => host.client.regenerate(selected.session, selected.creature, { ...branchOptions(message), signal })
      : (selected, signal) =>
          host.client.editMessage(selected.session, selected.creature, message.msgIdx, message.content, {
            ...branchOptions(message),
            signal,
          })
  return runBranch(host, message, start).then((data) => {
    host.post({ type: `${message.type}.result`, requestId: message.requestId, data })
  })
}

module.exports = { BRANCH_TYPES, dispatchBranch }
