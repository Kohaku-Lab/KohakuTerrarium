// Host-side model/slash + instance-metadata dispatch for the four fixed
// operations the shared model picker, slash menu and outer-context adapter use.
const { allowedMessage } = require('./protocol.cjs')

const MODEL_TYPES = new Set(['http.modelDirectory', 'http.commandInventory', 'http.switchModel', 'http.instanceMetadata'])

function ownsReady(runtime, readyId) {
  return !runtime.disposed && readyId === runtime.runtimeEpoch
}

function postResult(runtime, message, data) {
  runtime.post({ type: `${message.type}.result`, requestId: message.requestId, data })
}

// Admission is the target identity + ready epoch + explicit selection-intent
// version; the notification-ordering ``selectionVersion`` never fences it.
async function dispatchModel(runtime, message) {
  switch (message.type) {
    case 'http.modelDirectory': {
      if (!allowedMessage(message)) throw Error('Invalid model directory request')
      if (!ownsReady(runtime, message.readyId)) throw Error('Ready ownership changed')
      // Host-global read: the ready epoch is the only fence. Capture before the
      // await and re-check after so a ready reset suppresses the late result.
      const readyId = runtime.runtimeEpoch
      const data = await runtime.client.modelDirectory()
      if (!ownsReady(runtime, readyId)) throw Error('Runtime ownership changed')
      postResult(runtime, message, data)
      return
    }
    case 'http.commandInventory': {
      if (!allowedMessage(message)) throw Error('Invalid command inventory request')
      if (!ownsReady(runtime, message.readyId)) throw Error('Ready ownership changed')
      const selected = runtime.requireSelection(message)
      const readyId = runtime.runtimeEpoch
      const intent = runtime.selectionIntentVersion
      const data = await runtime.client.commandInventory(selected.session, selected.creature)
      if (!runtime.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
      postResult(runtime, message, data)
      return
    }
    case 'http.instanceMetadata': {
      if (!allowedMessage(message)) throw Error('Invalid instance metadata request')
      if (!ownsReady(runtime, message.readyId)) throw Error('Ready ownership changed')
      // Session-scoped read: the stable identity is the selected session, and the
      // exact selected object still fences the read against a ready reset.
      const selected = runtime.requireSelectedSession(message)
      const readyId = runtime.runtimeEpoch
      const intent = runtime.selectionIntentVersion
      const data = await runtime.client.instanceMetadata(selected.session)
      if (!runtime.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
      postResult(runtime, message, data)
      return
    }
    case 'http.switchModel': {
      if (!allowedMessage(message)) throw Error('Invalid model switch request')
      if (!ownsReady(runtime, message.readyId)) throw Error('Ready ownership changed')
      const selected = runtime.requireSelection(message)
      const readyId = runtime.runtimeEpoch
      const intent = runtime.selectionIntentVersion
      // A switch is a mutation: serialized with selection changes, admitted
      // before and after the await, and never retried.
      const data = await runtime.enqueueSelectionOperation(async () => {
        if (runtime.disposed || runtime.state.selection !== selected || intent !== runtime.selectionIntentVersion)
          throw Error('Selected Creature ownership changed')
        return runtime.client.switchModel(selected.session, selected.creature, message.model)
      })
      if (!runtime.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
      postResult(runtime, message, data)
      return
    }
    default:
      throw Error(`Unsupported model request: ${message.type}`)
  }
}

module.exports = { MODEL_TYPES, dispatchModel }
