// Regression: the shared model bridge must fail closed while a ready epoch is
// still being admitted. During a refresh the requested ``readyId`` advances
// before the selection is armed, so an owner that exposes ``admittedReadyId``
// (null mid-refresh) must never fall back to the latest requested readyId, and
// a captured delegate must emit nothing once the bridge is disposed.
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

test('a model request never falls back to the requested readyId while admittedReadyId is null', async () => {
  const { installModelBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'modelBridge.mjs')))

  const calls = []
  let settle = null
  const request = (type, data) => {
    calls.push({ type, data })
    return new Promise((resolve) => (settle = resolve))
  }
  // A refresh is outstanding: the requested epoch advanced, but no selection is armed yet.
  let owner = { readyId: 9, admittedReadyId: null }

  const uninstall = installModelBridge({ request, getOwner: () => owner })
  const switchModel = globalThis.__ktVsCodeSwitchModel
  try {
    await assert.rejects(switchModel('g', 'root', 'x'), /Wait for Session refresh/)
    await assert.rejects(globalThis.__ktVsCodeModelDirectory(), /Wait for Session refresh/)
    assert.deepEqual(calls, [], 'no request is emitted while the ready epoch is unadmitted')

    // Once admitted, the same epoch dispatches.
    owner = { readyId: 9, admittedReadyId: 9 }
    const sent = switchModel('g', 'root', 'x')
    assert.deepEqual(calls.at(-1), {
      type: 'http.switchModel',
      data: { session: 'g', creature: 'root', model: 'x', readyId: 9 },
    })

    // A ready/reconcile reset mid-flight revokes the admitted epoch.
    owner = { readyId: 10, admittedReadyId: null }
    settle({ status: 'switched' })
    await assert.rejects(sent, /ownership changed/)
  } finally {
    uninstall()
  }
})

test('a captured delegate emits nothing once the model bridge is disposed', async () => {
  const { installModelBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'modelBridge.mjs')))

  const calls = []
  const request = async (type, data) => {
    calls.push({ type, data })
    return {}
  }
  const uninstall = installModelBridge({ request, getOwner: () => ({ readyId: 5, admittedReadyId: 5 }) })
  const captured = globalThis.__ktVsCodeInstanceMetadata
  uninstall()

  await assert.rejects(captured('g'), /disposed/)
  assert.deepEqual(calls, [], 'a disposed bridge sends nothing')
})
