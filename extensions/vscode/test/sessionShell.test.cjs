const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

async function loadSessionShell() {
  const sourcePath = path.resolve(__dirname, '../src/webview/sessionShell.js')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${Date.now()}`
  return import(url)
}

test('session shell selects by stable Creature id and unbinds only after Host success', async () => {
  const { createSessionShell } = await loadSessionShell()
  const calls = []
  const session = {
    runtimeId: 'graph-one',
    title: 'Team',
    kind: 'terrarium',
    creatures: [
      { id: 'creature-a', name: 'same-name' },
      { id: 'creature-b', name: 'same-name' },
    ],
  }
  const chat = {
    unbindFromInstance: () => calls.push('unbind'),
    initForInstance: (_instance, options) => calls.push(['init', options.initialTab, options.autoReconnect]),
  }
  const api = {
    select: async (selection) => {
      calls.push(['select', selection])
      return { targetCreatureId: selection.creatureId }
    },
  }
  const shell = createSessionShell({ api, chat })

  const attached = await shell.open(session, 'creature-b')

  assert.deepEqual(calls, [['select', { session: 'graph-one', creatureId: 'creature-b' }], 'unbind', ['init', 'same-name', false]])
  assert.equal(attached.targetCreatureId, 'creature-b')
})

test('stop unbinds only after Host ownership is stopped', async () => {
  const { createSessionShell } = await loadSessionShell()
  const calls = []
  const shell = createSessionShell({
    api: {
      stop: async (selection) => calls.push(['stop', selection]),
    },
    chat: {
      unbindFromInstance: () => calls.push('unbind'),
      initForInstance() {},
    },
  })
  const current = {
    session: { runtimeId: 'graph-one' },
    targetCreatureId: 'creature-a',
  }

  await shell.stop(current)

  assert.deepEqual(calls, [['stop', { session: 'graph-one', creatureId: 'creature-a' }], 'unbind'])
})

test('resume attaches a single Creature by stable id and leaves multi-Creature sessions unselected', async () => {
  const { createSessionShell } = await loadSessionShell()
  const calls = []
  const sessions = {
    single: {
      runtimeId: 'graph-single',
      title: 'Single',
      kind: 'creature',
      creatures: [{ id: 'creature-single', name: 'single' }],
    },
    multi: {
      runtimeId: 'graph-multi',
      title: 'Multi',
      kind: 'terrarium',
      creatures: [
        { id: 'creature-a', name: 'a' },
        { id: 'creature-b', name: 'b' },
      ],
    },
  }
  const shell = createSessionShell({
    api: {
      resume: async (savedName) => sessions[savedName],
      clearSelection: async () => calls.push('clear'),
      select: async (selection) => {
        calls.push(selection)
        return { targetCreatureId: selection.creatureId }
      },
    },
    chat: {
      unbindFromInstance() {},
      initForInstance() {},
    },
  })

  const single = await shell.resume('single')
  const multi = await shell.resume('multi')

  assert.equal(single.targetCreatureId, 'creature-single')
  assert.deepEqual(calls, [{ session: 'graph-single', creatureId: 'creature-single' }, 'clear'])
  assert.deepEqual(multi, { session: sessions.multi, target: null, targetCreatureId: null })
})

test('restore consumes a Host-reconciled stable selection without selecting again', async () => {
  const { createSessionShell } = await loadSessionShell()
  const calls = []
  const session = {
    runtimeId: 'graph-new',
    title: 'Team',
    kind: 'terrarium',
    creatures: [{ id: 'creature-b', name: 'renamed' }],
  }
  const shell = createSessionShell({
    api: {
      select: async () => {
        throw Error('must not select again')
      },
    },
    chat: {
      unbindFromInstance: () => calls.push('unbind'),
      initForInstance: (_instance, options) => calls.push(['init', options.initialTab, options.autoReconnect]),
    },
  })

  const restored = shell.restore(session, {
    session: 'graph-new',
    creature: 'renamed',
    targetCreatureId: 'creature-b',
  })

  assert.deepEqual(calls, ['unbind', ['init', 'renamed', false]])
  assert.equal(restored.targetCreatureId, 'creature-b')
})

test('Host selection failure preserves the old production chat binding', async () => {
  const { createSessionShell } = await loadSessionShell()
  let unbound = false
  const shell = createSessionShell({
    api: {
      select: async () => {
        throw Error('selection failed')
      },
    },
    chat: {
      unbindFromInstance: () => {
        unbound = true
      },
      initForInstance() {},
    },
  })
  const session = {
    runtimeId: 'graph-one',
    title: 'Team',
    kind: 'terrarium',
    creatures: [{ id: 'creature-a', name: 'alpha' }],
  }

  await assert.rejects(shell.open(session, 'creature-a'), /selection failed/)
  assert.equal(unbound, false)
})
