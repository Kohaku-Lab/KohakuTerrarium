const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const sourcePath = path.resolve(__dirname, '..', 'src', 'webview', 'shims', 'visibility.js')

async function loadShim() {
  const source = fs.readFileSync(sourcePath, 'utf8')
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${Date.now()}-${Math.random()}`)
}

function fakeEnvironment(state = 'visible') {
  const original = {
    document: global.document,
    setInterval: global.setInterval,
    clearInterval: global.clearInterval,
    consoleError: console.error,
  }
  const listeners = new Set()
  const timers = []
  const cleared = []
  global.document = {
    visibilityState: state,
    addEventListener(type, listener) {
      if (type === 'visibilitychange') listeners.add(listener)
    },
    removeEventListener(type, listener) {
      if (type === 'visibilitychange') listeners.delete(listener)
    },
  }
  global.setInterval = (callback, interval) => {
    const timer = { callback, interval }
    timers.push(timer)
    return timer
  }
  global.clearInterval = (timer) => cleared.push(timer)
  console.error = () => {}
  return {
    timers,
    cleared,
    listeners,
    setVisibility(next) {
      global.document.visibilityState = next
      for (const listener of [...listeners]) listener()
    },
    restore() {
      global.document = original.document
      global.setInterval = original.setInterval
      global.clearInterval = original.clearInterval
      console.error = original.consoleError
    },
  }
}

test('visibility interval pauses, catches up once on resume, and cleans up idempotently', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let calls = 0
    const controller = createVisibilityInterval(() => calls++, 250)

    assert.deepEqual(Object.keys(controller).sort(), ['isRunning', 'start', 'stop'])
    controller.start()
    controller.start()
    assert.equal(controller.isRunning(), true)
    assert.equal(env.timers.length, 1)
    assert.equal(env.listeners.size, 1)

    env.setVisibility('hidden')
    assert.deepEqual(env.cleared, [env.timers[0]])
    env.setVisibility('hidden')
    assert.equal(calls, 0)

    env.setVisibility('visible')
    assert.equal(calls, 1)
    assert.equal(env.timers.length, 2)
    env.setVisibility('visible')
    assert.equal(calls, 1)
    assert.equal(env.timers.length, 2)

    controller.stop()
    controller.stop()
    assert.equal(controller.isRunning(), false)
    assert.equal(env.listeners.size, 0)
    assert.deepEqual(env.cleared, [env.timers[0], env.timers[1]])
  } finally {
    env.restore()
  }
})

test('visibility interval supports immediate start and contains callback errors', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let calls = 0
    const controller = createVisibilityInterval(
      () => {
        calls++
        throw new Error('boom')
      },
      100,
      { immediate: true },
    )

    assert.doesNotThrow(() => controller.start())
    assert.equal(calls, 1)
    assert.doesNotThrow(() => env.timers[0].callback())
    assert.equal(calls, 2)
    controller.stop()
  } finally {
    env.restore()
  }
})

test('visibility interval started hidden waits and catches up when visible', async () => {
  const env = fakeEnvironment('hidden')
  try {
    const { createVisibilityInterval } = await loadShim()
    let calls = 0
    const controller = createVisibilityInterval(() => calls++, 100, { immediate: true })
    controller.start()
    assert.equal(calls, 0)
    assert.equal(env.timers.length, 0)

    env.setVisibility('visible')
    assert.equal(calls, 1)
    assert.equal(env.timers.length, 1)
    controller.stop()
  } finally {
    env.restore()
  }
})
