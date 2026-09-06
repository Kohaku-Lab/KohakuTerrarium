const assert = require('node:assert/strict')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')

async function loadWithChoice(choice) {
  const calls = []
  const vscode = {
    window: {
      async showWarningMessage(...args) {
        calls.push(args)
        return choice
      },
    },
  }
  const originalLoad = Module._load
  Module._load = function (request, parent, isMain) {
    if (request === 'vscode') return vscode
    if (request === 'ws') return class {}
    return originalLoad(request, parent, isMain)
  }
  const extensionPath = path.resolve(__dirname, '../src/extension.cjs')
  delete require.cache[extensionPath]
  try {
    return { extension: require(extensionPath), calls }
  } finally {
    Module._load = originalLoad
  }
}

test('clear context uses an explicit native modal confirmation', async () => {
  const { extension, calls } = await loadWithChoice('Clear Context')
  assert.equal(await extension.confirmContextClear(), true)
  assert.deepEqual(calls[0], ['Clear the active Creature context? Session history remains available.', { modal: true }, 'Clear Context'])
})

test('clear context cancellation fails closed', async () => {
  const { extension } = await loadWithChoice(undefined)
  assert.equal(await extension.confirmContextClear(), false)
})

function runtimeHarness() {
  let owned = true
  const calls = []
  const runtime = {
    acquireContextCommand: () => ({ capability: true }),
    ownsContextCommand: () => owned,
    async handle(message) {
      calls.push(message)
    },
  }
  return {
    runtime,
    calls,
    supersede: () => {
      owned = false
    },
  }
}

test('clear captures runtime and selection ownership before confirmation', async () => {
  const { extension } = await loadWithChoice(undefined)
  const first = runtimeHarness()
  let current = first.runtime
  let resolveConfirmation
  const confirmation = new Promise((resolve) => {
    resolveConfirmation = resolve
  })
  const posts = []

  const pending = extension.dispatchContextCommand({
    message: { type: 'context.clear', requestId: 7 },
    getRuntime: async () => first.runtime,
    isCurrent: (runtime) => runtime === current,
    confirmClear: () => confirmation,
    post: async (message) => posts.push(message),
  })
  await Promise.resolve()
  first.supersede()
  current = runtimeHarness().runtime
  resolveConfirmation(true)
  await pending

  assert.deepEqual(first.calls, [])
  assert.deepEqual(posts, [
    {
      type: 'context.clear.result',
      requestId: 7,
      data: { cancelled: true, superseded: true },
    },
  ])
})

test('clear executes only for the same confirmed ownership and cancel never executes', async () => {
  const { extension } = await loadWithChoice(undefined)
  const owned = runtimeHarness()
  const args = {
    getRuntime: async () => owned.runtime,
    isCurrent: (runtime) => runtime === owned.runtime,
    post: async () => {},
  }
  await extension.dispatchContextCommand({
    ...args,
    message: { type: 'context.clear', requestId: 8 },
    confirmClear: async () => true,
  })
  await extension.dispatchContextCommand({
    ...args,
    message: { type: 'context.clear', requestId: 9 },
    confirmClear: async () => false,
  })
  assert.equal(owned.calls.length, 1)
  assert.equal(owned.calls[0].type, 'context.clear')
})
