// D1 fixed branch-mutation transport: strict protocol, fixed-route client
// builders, RuntimeHost ready/target fences with a non-blocking admission queue,
// the named webview facade, and the webview request lifecycle timeout.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { createClient } = require('../src/host/client.cjs')
const { RuntimeHost } = require('../src/host/runtime.cjs')

const root = path.resolve(__dirname, '..')

test('strict protocol accepts only well-formed regenerate/edit messages', () => {
  const locator = { eventId: 9, turnIndex: 2, branchId: 1 }
  const valid = [
    { type: 'http.regenerate', requestId: 1, session: 'g', creature: 'root', readyId: 7 },
    {
      type: 'http.regenerate',
      requestId: 2,
      session: 'g',
      creature: 'root',
      readyId: 7,
      turnIndex: 3,
      branchView: { 1: 2 },
      correlationId: 'c1',
      locator,
    },
    { type: 'http.editMessage', requestId: 3, session: 'g', creature: 'root', readyId: 7, msgIdx: 0, content: 'hi' },
    {
      type: 'http.editMessage',
      requestId: 4,
      session: 'g',
      creature: 'root',
      readyId: 7,
      msgIdx: 4,
      content: [{ type: 'text', text: 'hi' }],
      turnIndex: 1,
      userPosition: 0,
      branchView: { 1: 2 },
      correlationId: 'c2',
      locator,
    },
  ]
  for (const message of valid) assert.equal(allowedMessage(message), true, JSON.stringify(message))

  const invalid = [
    // ready epoch, target identities and the fixed index are mandatory
    { type: 'http.regenerate', requestId: 5, session: 'g', creature: 'root' },
    { type: 'http.regenerate', requestId: 6, creature: 'root', readyId: 7 },
    { type: 'http.regenerate', requestId: 7, session: '', creature: 'root', readyId: 7 },
    { type: 'http.editMessage', requestId: 8, session: 'g', creature: 'root', readyId: 7, content: 'x' },
    { type: 'http.editMessage', requestId: 9, session: 'g', creature: 'root', readyId: 7, msgIdx: -1, content: 'x' },
    { type: 'http.editMessage', requestId: 10, session: 'g', creature: 'root', readyId: 7, msgIdx: 1.5, content: 'x' },
    // content is text or an already-serialized parts array, never a File blob
    { type: 'http.editMessage', requestId: 11, session: 'g', creature: 'root', readyId: 7, msgIdx: 0, content: 5 },
    { type: 'http.editMessage', requestId: 12, session: 'g', creature: 'root', readyId: 7, msgIdx: 0, content: [new Uint8Array([1])] },
    // a persisted locator carries positive durable ids only
    {
      type: 'http.regenerate',
      requestId: 13,
      session: 'g',
      creature: 'root',
      readyId: 7,
      locator: { eventId: 0, turnIndex: 1, branchId: 1 },
    },
    { type: 'http.regenerate', requestId: 14, session: 'g', creature: 'root', readyId: 7, locator: { eventId: 1, turnIndex: 1 } },
    {
      type: 'http.regenerate',
      requestId: 15,
      session: 'g',
      creature: 'root',
      readyId: 7,
      locator: { eventId: 1, turnIndex: 1, branchId: 1, url: '/x' },
    },
    // branch_view is a numeric map, correlation a non-empty string
    { type: 'http.regenerate', requestId: 16, session: 'g', creature: 'root', readyId: 7, branchView: { a: 1 } },
    { type: 'http.regenerate', requestId: 17, session: 'g', creature: 'root', readyId: 7, branchView: { 1: 'x' } },
    { type: 'http.regenerate', requestId: 18, session: 'g', creature: 'root', readyId: 7, correlationId: '' },
    { type: 'http.regenerate', requestId: 19, session: 'g', creature: 'root', readyId: 7, turnIndex: -1 },
    // a compromised webview cannot name its own URL/method/header
    { type: 'http.regenerate', requestId: 20, session: 'g', creature: 'root', readyId: 7, url: '/etc/passwd' },
    { type: 'http.editMessage', requestId: 21, session: 'g', creature: 'root', readyId: 7, msgIdx: 0, content: 'x', method: 'POST' },
    { type: 'http.editMessage', requestId: 22, session: 'g', creature: 'root', readyId: 7, msgIdx: 0, content: 'x', headers: {} },
    { type: 'http.regenerate', requestId: 23, session: 'g', creature: 'root', readyId: 0 },
    { type: 'http.regenerate', requestId: 24, session: 'g', creature: 'root', readyId: '7' },
  ]
  for (const message of invalid) assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('Host client builds the exact encoded fixed route and body for each branch operation', async () => {
  const calls = []
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: 'host-secret',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => ({ status: 'completed', turn_index: 1, branch_id: 2, parent_branch_path: [] }) }
    },
  })

  await client.regenerate('graph.1', 'root/x y', {
    turnIndex: 3,
    branchView: { 1: 2 },
    correlationId: 'c1',
    locator: { eventId: 9, turnIndex: 2, branchId: 1 },
  })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/graph.1/creatures/root%2Fx%20y/regenerate')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers['X-KT-Host-Token'], 'host-secret')
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json')
  assert.equal(calls[0].options.redirect, 'error')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    turn_index: 3,
    branch_view: { 1: 2 },
    request_id: 'c1',
    target: { event_id: 9, turn_index: 2, branch_id: 1 },
  })

  calls.length = 0
  await client.editMessage('graph.1', 'root/x y', 4, [{ type: 'text', text: 'hi' }], {
    turnIndex: 1,
    userPosition: 0,
    branchView: { 1: 2 },
    correlationId: 'c2',
    locator: { eventId: 9, turnIndex: 2, branchId: 1 },
  })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/graph.1/creatures/root%2Fx%20y/messages/4/edit')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    content: [{ type: 'text', text: 'hi' }],
    turn_index: 1,
    user_position: 0,
    branch_view: { 1: 2 },
    request_id: 'c2',
    target: { event_id: 9, turn_index: 2, branch_id: 1 },
  })

  // A bare regenerate omits every nullable field rather than sending nulls.
  calls.length = 0
  await client.regenerate('g', 'root', {})
  assert.deepEqual(JSON.parse(calls[0].options.body), {})
})

function json(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function withServer() {
  const requests = []
  const releases = []
  let handler = null
  const server = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      requests.push({ method: req.method, url: req.url, token: req.headers['x-kt-host-token'], body: raw ? JSON.parse(raw) : null })
      if (handler) handler(req, res)
      else json(res, 200, {})
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    server,
    requests,
    releases,
    setHandler(fn) {
      handler = fn
    },
    waitForRequest(count) {
      return new Promise((resolve) => {
        const check = () => {
          if (requests.length >= count) {
            clearInterval(timer)
            resolve()
          }
        }
        const timer = setInterval(check, 2)
        check()
      })
    },
    close() {
      return new Promise((resolve) => server.close(resolve))
    },
  }
}

function makeRuntime(origin, runtimeEpoch = 100) {
  const posts = []
  const state = {
    selection: null,
    async updateSelection(selection) {
      this.selection = selection
    },
    async updateSelectionIf(selection, owns) {
      if (!owns()) return false
      this.selection = selection
      return true
    },
  }
  const host = new RuntimeHost({
    client: createClient({ endpoint: origin, token: 'host-secret', fetchImpl: fetch }),
    state,
    sockets: { begin: () => 1, open() {}, send: () => true, closeSocket() {}, closeGeneration() {} },
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt/x',
    getWorkspacePath: () => 'C:/ws',
    socketFactory: () => ({}),
    webSocketBase: 'ws://127.0.0.1:1',
    token: 'host-secret',
    runtimeEpoch,
  })
  return { host, state, posts }
}

function selection() {
  return { session: 'graph_1', creature: 'root/x y', targetCreatureId: 'c1' }
}

const COMPLETED = { status: 'completed', request_id: 'c1', turn_index: 2, branch_id: 5, parent_branch_path: [] }

test('the runtime routes regenerate and edit over their fixed POST routes with the exact body', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 200, COMPLETED))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({
      type: 'http.regenerate',
      requestId: 1,
      session: 'graph_1',
      creature: 'root/x y',
      readyId: 100,
      turnIndex: 3,
      correlationId: 'c1',
      locator: { eventId: 9, turnIndex: 2, branchId: 1 },
    })
    await host.handle({
      type: 'http.editMessage',
      requestId: 2,
      session: 'graph_1',
      creature: 'root/x y',
      readyId: 100,
      msgIdx: 4,
      content: 'edited',
      correlationId: 'c2',
    })

    assert.deepEqual(
      srv.requests.map((request) => request.url),
      ['/api/sessions/graph_1/creatures/root%2Fx%20y/regenerate', '/api/sessions/graph_1/creatures/root%2Fx%20y/messages/4/edit'],
    )
    assert.deepEqual(srv.requests[0].body, { turn_index: 3, request_id: 'c1', target: { event_id: 9, turn_index: 2, branch_id: 1 } })
    assert.deepEqual(srv.requests[1].body, { content: 'edited', request_id: 'c2' })
    assert.deepEqual(posts, [
      { type: 'http.regenerate.result', requestId: 1, data: COMPLETED },
      { type: 'http.editMessage.result', requestId: 2, data: COMPLETED },
    ])
  } finally {
    await srv.close()
  }
})

test('a malformed or stale branch envelope is rejected before any network side effect', async () => {
  const srv = await withServer()
  try {
    const { host, state } = makeRuntime(srv.origin)
    state.selection = selection()

    for (const message of [
      { type: 'http.regenerate', requestId: 3, session: 'graph_1', creature: 'root/x y', readyId: 100, url: '/x' },
      { type: 'http.editMessage', requestId: 4, session: 'graph_1', creature: 'root/x y', readyId: 100, msgIdx: -1, content: 'x' },
    ]) {
      await assert.rejects(host.handle(message), /Invalid/)
    }
    assert.equal(srv.requests.length, 0, 'no malformed request reached the network')

    state.selection = null
    await assert.rejects(
      host.handle({ type: 'http.regenerate', requestId: 5, session: 'graph_1', creature: 'root/x y', readyId: 100 }),
      /ownership changed/,
    )
    assert.equal(srv.requests.length, 0)

    state.selection = selection()
    await assert.rejects(
      host.handle({ type: 'http.regenerate', requestId: 6, session: 'graph_1', creature: 'root/x y', readyId: 99 }),
      /Ready ownership changed/,
    )
    assert.equal(srv.requests.length, 0, 'a stale ready epoch never reaches the network')
  } finally {
    await srv.close()
  }
})

test('a refused branch POST retains the exact HTTP status and reports mayHaveRun', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 409, { detail: 'Cannot mutate conversation history while a turn is active' }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await assert.rejects(
      host.handle({ type: 'http.regenerate', requestId: 7, session: 'graph_1', creature: 'root/x y', readyId: 100 }),
      (error) => {
        assert.equal(error.status, 409)
        assert.equal(error.mayHaveRun, true)
        return true
      },
    )
    assert.equal(srv.requests.length, 1, 'a mutation is never retried')
    assert.deepEqual(posts, [], 'a refused branch operation posts no result')
  } finally {
    await srv.close()
  }
})

test('a lost response after dispatch reports mayHaveRun with an unknown status', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => req.socket.destroy())
    const { host, state } = makeRuntime(srv.origin)
    state.selection = selection()

    await assert.rejects(
      host.handle({ type: 'http.regenerate', requestId: 8, session: 'graph_1', creature: 'root/x y', readyId: 100 }),
      (error) => {
        assert.equal(error.status, undefined)
        assert.equal(error.mayHaveRun, true)
        return true
      },
    )
    assert.equal(srv.requests.length, 1, 'a dispatched mutation is never retried')
  } finally {
    await srv.close()
  }
})

test('the long branch POST releases the selection queue before it settles and cannot paint a new target', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => {
      if (req.url.endsWith('/regenerate')) return srv.releases.push(() => json(res, 200, COMPLETED))
      if (req.url.startsWith('/api/sessions/active/'))
        return json(res, 200, { session_id: 'graph_1', type: 'terrarium', creatures: [{ creature_id: 'c2', name: 'beta' }] })
      return json(res, 200, {})
    })
    const { host, state, posts } = makeRuntime(srv.origin, 100)
    state.selection = selection()

    const regen = host.handle({ type: 'http.regenerate', requestId: 9, session: 'graph_1', creature: 'root/x y', readyId: 100 })
    await srv.waitForRequest(1)

    // The queue is released, so a select can complete while the POST is pending.
    await host.handle({ type: 'session.select', requestId: 10, session: 'graph_1', creatureId: 'c2' })
    assert.equal(state.selection.targetCreatureId, 'c2')

    srv.releases.shift()()
    await assert.rejects(regen, /ownership changed/)
    assert.equal(posts.filter((post) => post.requestId === 9).length, 0, 'an old completion never paints the new target')
  } finally {
    await srv.close()
  }
})

test('a branch POST queued behind a blocked admission sends no HTTP when ownership changes first', async () => {
  const srv = await withServer()
  try {
    for (const change of [(host) => host.beginReady(200), (host) => host.selectionIntentVersion++]) {
      const { host, state } = makeRuntime(srv.origin, 100)
      state.selection = selection()
      let release
      host.selectionOperationTail = new Promise((resolve) => (release = resolve))
      const regen = host.handle({ type: 'http.regenerate', requestId: 11, session: 'graph_1', creature: 'root/x y', readyId: 100 })
      change(host)
      release()
      await assert.rejects(regen, (error) => {
        assert.equal(error.mayHaveRun, false)
        return true
      })
    }
    assert.equal(srv.requests.length, 0, 'a superseded pre-admission branch op never POSTs')
  } finally {
    await srv.close()
  }
})

test('a started branch POST is neither retried nor reported as success after supersession', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, COMPLETED)))
    const { host, state, posts } = makeRuntime(srv.origin, 100)
    state.selection = selection()

    const regen = host.handle({ type: 'http.regenerate', requestId: 12, session: 'graph_1', creature: 'root/x y', readyId: 100 })
    await srv.waitForRequest(1)
    host.beginReady(200)
    srv.releases.shift()()
    await assert.rejects(regen, (error) => {
      assert.equal(error.mayHaveRun, true)
      assert.equal(error.superseded, true)
      return true
    })
    assert.equal(srv.requests.length, 1, 'a started mutation is never retried')
    assert.deepEqual(posts, [], 'a superseded branch op is not reported as success')
  } finally {
    await srv.close()
  }
})

test('dispose aborts the local branch wait without dispatching a backend interrupt', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, COMPLETED)))
    const { host, state } = makeRuntime(srv.origin, 100)
    state.selection = selection()

    const regen = host.handle({ type: 'http.regenerate', requestId: 13, session: 'graph_1', creature: 'root/x y', readyId: 100 })
    await srv.waitForRequest(1)
    host.dispose()
    await assert.rejects(regen, (error) => {
      assert.equal(error.mayHaveRun, true)
      return true
    })
    assert.equal(host.branchControllers.size, 0, 'dispose leaves no owned branch controller behind')
    assert.equal(srv.requests.length, 1, 'no automatic interrupt POST follows dispose')
  } finally {
    await srv.close()
  }
})

test('the named branch facade delegates, presents status, and revokes on dispose', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  const { installBranchBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'branchBridge.mjs')))

  const calls = []
  let failure = null
  const request = async (type, data, onSend, options) => {
    calls.push({ type, data, timeoutMs: options?.timeoutMs })
    if (failure) throw failure
    return COMPLETED
  }

  const uninstall = installBranchBridge({ request, getOwner: () => ({ admittedReadyId: 100 }) })
  try {
    await shim.agentAPI.regenerate('g', 'root', {
      turnIndex: 3,
      branchView: { 1: 2 },
      requestId: 'c1',
      locator: { eventId: 9, turnIndex: 2, branchId: 1 },
    })
    assert.deepEqual(calls.at(-1), {
      type: 'http.regenerate',
      data: {
        session: 'g',
        creature: 'root',
        turnIndex: 3,
        branchView: { 1: 2 },
        correlationId: 'c1',
        locator: { eventId: 9, turnIndex: 2, branchId: 1 },
        readyId: 100,
      },
      timeoutMs: 0,
    })

    await shim.agentAPI.editMessage('g', 'root', 4, 'edited', { turnIndex: 1, userPosition: 0, requestId: 'c2' })
    assert.deepEqual(calls.at(-1), {
      type: 'http.editMessage',
      data: {
        session: 'g',
        creature: 'root',
        msgIdx: 4,
        content: 'edited',
        turnIndex: 1,
        userPosition: 0,
        correlationId: 'c2',
        readyId: 100,
      },
      timeoutMs: 0,
    })

    failure = Object.assign(Error('branch_failed'), { status: 409, mayHaveRun: true })
    await assert.rejects(shim.agentAPI.regenerate('g', 'root', {}), (error) => {
      assert.equal(error.response.status, 409)
      assert.equal(error.status, 409)
      assert.equal(error.mayHaveRun, true)
      return true
    })
  } finally {
    uninstall()
  }

  for (const name of ['__ktVsCodeRegenerate', '__ktVsCodeEditMessage']) assert.equal(globalThis[name], undefined, name)
  await assert.rejects(shim.agentAPI.regenerate('g', 'root', {}), /bridge is unavailable/)
  await assert.rejects(shim.agentAPI.editMessage('g', 'root', 0, 'x'), /bridge is unavailable/)
  await assert.rejects(shim.agentAPI.rewindTo('g', 'root', 1), /unavailable/)
})

test('the branch facade is fenced by the ready owner and refuses a captured delegate after dispose', async () => {
  const { installBranchBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'branchBridge.mjs')))

  const calls = []
  let owner = { admittedReadyId: null }
  let settle = null
  const request = (type, data) => {
    calls.push({ type, data })
    return new Promise((resolve) => (settle = resolve))
  }

  const uninstall = installBranchBridge({ request, getOwner: () => owner })
  const captured = globalThis.__ktVsCodeRegenerate
  try {
    await assert.rejects(globalThis.__ktVsCodeRegenerate('g', 'root', {}), /Wait for Session refresh/)
    assert.deepEqual(calls, [])

    owner = { admittedReadyId: 42 }
    const sent = captured('g', 'root', {})
    assert.equal(calls.at(-1).type, 'http.regenerate')
    settle(COMPLETED)
    assert.deepEqual(await sent, COMPLETED)
  } finally {
    uninstall()
  }

  const before = calls.length
  await assert.rejects(captured('g', 'root', {}), /disposed/)
  assert.equal(calls.length, before, 'a disposed facade emits no request')
})

test('the webview request lifecycle gives branch ops no timeout and others the default budget', async () => {
  const { createRequestLifecycle } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'requestLifecycle.mjs')))
  const timers = []
  const sent = []
  const lifecycle = createRequestLifecycle({
    postMessage: (message) => sent.push(message),
    setTimer: (fn, ms) => {
      const handle = { fn, ms }
      timers.push(handle)
      return handle
    },
    clearTimer: (handle) => {
      const index = timers.indexOf(handle)
      if (index !== -1) timers.splice(index, 1)
    },
  })

  lifecycle.request('http.regenerate', {}, () => {}, { timeoutMs: 0 })
  assert.equal(timers.length, 0, 'a branch POST is never abandoned by a client timeout')

  lifecycle.request('http.history', { session: 'g', creature: 'root' })
  assert.deepEqual(
    timers.map((handle) => handle.ms),
    [30000],
    'ordinary requests keep the 30s budget',
  )

  // A settled branch request frees its slot without a stale timer.
  const message = sent.find((entry) => entry.type === 'http.regenerate')
  assert.equal(lifecycle.settle({ type: 'http.regenerate.result', requestId: message.requestId, data: COMPLETED }), true)
  assert.equal(lifecycle.size(), 1)
})
