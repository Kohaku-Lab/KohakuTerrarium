const assert = require('node:assert/strict')
const test = require('node:test')

function fixture() {
  const owned = { readyId: 1, runtimeId: 'a', creatureId: 'one', name: 'alpha' }
  const context = { id: 'context' }
  const added = []
  const released = []
  const calls = []
  return {
    added,
    released,
    calls,
    options: {
      chat: {
        activeTab: 'alpha',
        registerCommandResultContext: () => context,
        addCommandResult: (...args) => added.push(args),
        releaseCommandResultContext: (...args) => released.push(args),
      },
      request: async (...args) => {
        calls.push(args)
        return { success: true, output: 'ok' }
      },
      ownership: { isCurrent: () => true, dispatch: (operation) => operation(() => {}) },
      getTarget: () => owned,
      getFence: () => ({ readyId: 1, selectionVersion: 0 }),
    },
  }
}

test('goal result hands anchoring lifecycle to the store, but rejected requests release unused contexts', async () => {
  const { installGoalBridge } = await import('../src/webview/goalBridge.mjs')
  const { options, added, released } = fixture()
  const uninstall = installGoalBridge(options)
  try {
    await globalThis.__ktVsCodeGoal('a', 'alpha', 'list')
    assert.equal(added.length, 1)
    assert.equal(released.length, 0, 'the store owns a surfaced context until a following transcript event anchors it')
    options.request = async () => {
      throw Error('network failed')
    }
    uninstall()
    const uninstallFailed = installGoalBridge(options)
    try {
      await assert.rejects(globalThis.__ktVsCodeGoal('a', 'alpha', 'list'))
      assert.equal(released.length, 1)
    } finally {
      uninstallFailed()
    }
  } finally {
    uninstall()
  }
})
