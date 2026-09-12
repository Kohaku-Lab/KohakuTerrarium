const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

// The alias boundary now re-exports the production pure helper, so this loads
// the real `.mjs` (whose relative import Node resolves) rather than a data-URI
// copy. A cache-busting query keeps each test on a fresh module instance.
const sourcePath = path.resolve(__dirname, '..', 'src', 'webview', 'shims', 'visibility.mjs')

async function loadShim() {
  return import(`${pathToFileURL(sourcePath).href}?v=${Date.now()}-${Math.random()}`)
}

function flushMicrotasks() {
  // Two rounds settle the callback promise and the settled-handler chained to it.
  return Promise.resolve().then(() => Promise.resolve())
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
    lastTimer() {
      return timers[timers.length - 1]
    },
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

// RED before the fix: the old shim dropped the callback's returned promise, so
// a slow backend stacked one overlapping 1.5s transcript poll per tick. The
// in-flight skip makes the second synchronous tick a no-op.
test('skips ticks while the previous async callback is still in flight', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let resolveFirst
    let calls = 0
    const controller = createVisibilityInterval(() => {
      calls++
      return new Promise((resolve) => {
        resolveFirst = resolve
      })
    }, 1500)

    controller.start()
    const timer = env.lastTimer()
    timer.callback()
    timer.callback()
    assert.equal(calls, 1)

    resolveFirst()
    await flushMicrotasks()
    timer.callback()
    assert.equal(calls, 2)
    controller.stop()
  } finally {
    env.restore()
  }
})

test('keeps polling after the in-flight callback rejects', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let rejectFirst
    let calls = 0
    const controller = createVisibilityInterval(() => {
      calls++
      return new Promise((_resolve, reject) => {
        rejectFirst = reject
      })
    }, 1500)

    controller.start()
    const timer = env.lastTimer()
    timer.callback()
    timer.callback()
    assert.equal(calls, 1)

    rejectFirst(new Error('boom'))
    await flushMicrotasks()
    timer.callback()
    assert.equal(calls, 2)
    controller.stop()
  } finally {
    env.restore()
  }
})

test('stop() disposes the in-flight guard so a restart polls immediately', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let resolveFirst
    let calls = 0
    const controller = createVisibilityInterval(() => {
      calls++
      return new Promise((resolve) => {
        resolveFirst = resolve
      })
    }, 1500)

    controller.start()
    env.timers[0].callback()
    assert.equal(calls, 1)

    controller.stop()
    controller.start()
    env.timers[1].callback()
    assert.equal(calls, 2)

    resolveFirst()
    await flushMicrotasks()
    controller.stop()
  } finally {
    env.restore()
  }
})

test('a promise settling from a previous run never clears the current run guard', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    const resolvers = []
    let calls = 0
    const controller = createVisibilityInterval(() => {
      calls++
      return new Promise((resolve) => {
        resolvers.push(resolve)
      })
    }, 1500)

    controller.start()
    env.timers[0].callback()
    assert.equal(calls, 1)

    controller.stop()
    controller.start()
    env.timers[1].callback()
    assert.equal(calls, 2)

    // The stale first-run promise settles — it must not free the live guard.
    resolvers[0]()
    await flushMicrotasks()
    env.timers[1].callback()
    assert.equal(calls, 2)

    resolvers[1]()
    await flushMicrotasks()
    env.timers[1].callback()
    assert.equal(calls, 3)
    controller.stop()
  } finally {
    env.restore()
  }
})

test('visibility resume skips the catch-up tick while a request is in flight', async () => {
  const env = fakeEnvironment()
  try {
    const { createVisibilityInterval } = await loadShim()
    let resolveFirst
    let calls = 0
    const controller = createVisibilityInterval(() => {
      calls++
      return new Promise((resolve) => {
        resolveFirst = resolve
      })
    }, 1500)

    controller.start()
    env.timers[0].callback()
    assert.equal(calls, 1)

    env.setVisibility('hidden')
    env.setVisibility('visible')
    // Resume fires a catch-up tick, but the in-flight request skips it and the
    // interval is re-armed instead of stacking a duplicate.
    assert.equal(calls, 1)
    assert.equal(env.timers.length, 2)

    resolveFirst()
    await flushMicrotasks()
    env.timers[1].callback()
    assert.equal(calls, 2)
    controller.stop()
  } finally {
    env.restore()
  }
})
