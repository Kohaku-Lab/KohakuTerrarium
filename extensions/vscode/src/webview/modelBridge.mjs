// Installs the Host request facades the shared model-picker/config leaves call.
// Each delegate maps to one fixed route, fenced by the ready owner and revoked on dispose.
export function installModelBridge({ request, getOwner }) {
  let disposed = false

  // A host may expose ``admittedReadyId`` — the ready epoch whose selection is
  // actually armed (the requested epoch has been admitted AND its selection is
  // live). During a ready/reconcile BEFORE ``ready.result`` that is null, so a
  // model request never dispatches on a stale selection. Hosts that only expose
  // the requested ``readyId`` keep the previous behavior.
  function readyIdOf(owner) {
    return owner?.admittedReadyId !== undefined ? owner.admittedReadyId : owner?.readyId
  }

  async function send(type, data) {
    if (disposed) throw Error('Model bridge is disposed')
    const readyId = readyIdOf(getOwner?.())
    if (!Number.isSafeInteger(readyId) || readyId < 1) throw Error('Wait for Session refresh')
    const result = await request(type, { ...data, readyId })
    if (disposed || readyIdOf(getOwner?.()) !== readyId) throw Error('Session ready ownership changed')
    return result
  }

  const modelDirectory = () => send('http.modelDirectory', {})
  const commandInventory = (session, creature) => send('http.commandInventory', { session, creature })
  const switchModel = (session, creature, model) => send('http.switchModel', { session, creature, model })
  const instanceMetadata = (session) => send('http.instanceMetadata', { session })

  globalThis.__ktVsCodeModelDirectory = modelDirectory
  globalThis.__ktVsCodeCommandInventory = commandInventory
  globalThis.__ktVsCodeSwitchModel = switchModel
  globalThis.__ktVsCodeInstanceMetadata = instanceMetadata

  return () => {
    disposed = true
    if (globalThis.__ktVsCodeModelDirectory === modelDirectory) delete globalThis.__ktVsCodeModelDirectory
    if (globalThis.__ktVsCodeCommandInventory === commandInventory) delete globalThis.__ktVsCodeCommandInventory
    if (globalThis.__ktVsCodeSwitchModel === switchModel) delete globalThis.__ktVsCodeSwitchModel
    if (globalThis.__ktVsCodeInstanceMetadata === instanceMetadata) delete globalThis.__ktVsCodeInstanceMetadata
  }
}
