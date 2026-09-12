const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

async function loadSessionActions() {
  const sourcePath = path.resolve(__dirname, '../src/webview/sessionActions.mjs')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${Date.now()}`
  return import(url)
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const SESSION = {
  runtimeId: 'graph-one',
  title: 'Team',
  kind: 'terrarium',
  creatures: [
    { id: 'c1', name: 'root' },
    { id: 'c2', name: 'beta' },
  ],
}

const owner = (target, targetCreatureId) => ({ value: { session: SESSION, target, targetCreatureId } })

test('a target change reuses shell.open and commits the current selection result', async () => {
  const { createTargetSelector } = await loadSessionActions()
  const currentSession = owner('root', 'c1')
  const error = { value: '' }
  const opened = []
  const shell = {
    open: async (session, creatureId) => {
      opened.push(creatureId)
      return { session, target: 'beta', targetCreatureId: creatureId }
    },
  }
  const selectTarget = createTargetSelector({ shell, currentSession, error, getReadyId: () => 1000, getOperationEpoch: () => 1 })

  await selectTarget('beta')

  assert.deepEqual(opened, ['c2'])
  assert.equal(currentSession.value.targetCreatureId, 'c2')
  assert.equal(error.value, '')
})

test('an unknown target name is a no-op and an unknown session commits nothing', async () => {
  const { createTargetSelector } = await loadSessionActions()
  const currentSession = owner('root', 'c1')
  const error = { value: '' }
  let opened = 0
  const shell = {
    open: async (session, creatureId) => {
      opened += 1
      return { session, target: creatureId, targetCreatureId: creatureId }
    },
  }
  const selectTarget = createTargetSelector({ shell, currentSession, error, getReadyId: () => 1000, getOperationEpoch: () => 1 })

  await selectTarget('nope')
  assert.equal(opened, 0)
  assert.equal(currentSession.value.targetCreatureId, 'c1')

  const empty = createTargetSelector({ shell, currentSession: { value: null }, error, getReadyId: () => 1000, getOperationEpoch: () => 1 })
  await empty('root')
  assert.equal(opened, 0)
})

test('a superseded target change never commits its stale result', async () => {
  const { createTargetSelector } = await loadSessionActions()
  const currentSession = owner('root', 'c1')
  const error = { value: '' }
  const first = deferred()
  let call = 0
  const shell = {
    open: async (session, creatureId) => {
      call += 1
      if (call === 1) await first.promise
      return { session, target: creatureId === 'c2' ? 'beta' : 'root', targetCreatureId: creatureId }
    },
  }
  const selectTarget = createTargetSelector({ shell, currentSession, error, getReadyId: () => 1000, getOperationEpoch: () => 1 })

  const stale = selectTarget('beta')
  await selectTarget('root')
  assert.equal(currentSession.value.targetCreatureId, 'c1')

  first.resolve()
  await stale
  assert.equal(currentSession.value.targetCreatureId, 'c1', 'the stale beta result never replaced the newest root')
  assert.equal(error.value, '')
})

test('a failed select reports only for the still-current target', async () => {
  const { createTargetSelector } = await loadSessionActions()
  const currentSession = owner('root', 'c1')
  const error = { value: '' }
  let shouldThrow = true
  const shell = {
    open: async () => {
      if (shouldThrow) throw Error('selection failed')
      return { session: SESSION, target: 'beta', targetCreatureId: 'c2' }
    },
  }
  const selectTarget = createTargetSelector({ shell, currentSession, error, getReadyId: () => 1000, getOperationEpoch: () => 1 })

  await selectTarget('beta')
  assert.equal(error.value, 'selection failed')

  // A newer selection supersedes the failed one before its rejection lands.
  shouldThrow = false
  const stale = deferred()
  let call = 0
  const shell2 = {
    open: async (session, creatureId) => {
      call += 1
      if (call === 1) {
        await stale.promise
        throw Error('stale failure')
      }
      return { session, target: 'root', targetCreatureId: creatureId }
    },
  }
  const error2 = { value: '' }
  const selectTarget2 = createTargetSelector({
    shell: shell2,
    currentSession,
    error: error2,
    getReadyId: () => 1000,
    getOperationEpoch: () => 1,
  })
  const failed = selectTarget2('beta')
  await selectTarget2('root')
  stale.resolve()
  await failed
  assert.equal(error2.value, '', 'a superseded failure is not reported')
})
