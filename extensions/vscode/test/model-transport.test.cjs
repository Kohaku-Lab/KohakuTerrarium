// M1 model/slash + instance-metadata transport: strict protocol, fixed-route
// client builders, RuntimeHost ready/target fences, and the named webview facade.
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

test('strict protocol accepts only well-formed model/command/instance messages', () => {
  const valid = [
    { type: 'http.modelDirectory', requestId: 1, readyId: 7 },
    { type: 'http.commandInventory', requestId: 2, session: 'graph-1', creature: 'root', readyId: 7 },
    { type: 'http.switchModel', requestId: 3, session: 'graph-1', creature: 'root', model: 'openai/gpt-5@effort=high', readyId: 7 },
    { type: 'http.instanceMetadata', requestId: 4, session: 'graph-1', readyId: 7 },
  ]
  for (const message of valid) assert.equal(allowedMessage(message), true, JSON.stringify(message))

  const invalid = [
    // model directory is host-global: no target identity may be smuggled in
    { type: 'http.modelDirectory', requestId: 5, session: 'graph-1', readyId: 7 },
    { type: 'http.modelDirectory', requestId: 6, url: '/etc/passwd', readyId: 7 },
    // live command inventory needs both target identities and no extra keys
    { type: 'http.commandInventory', requestId: 7, session: 'graph-1', readyId: 7 },
    { type: 'http.commandInventory', requestId: 8, session: '', creature: 'root', readyId: 7 },
    { type: 'http.commandInventory', requestId: 9, session: 'g', creature: 'root', url: '/x', readyId: 7 },
    // a switch carries the canonical selector in the body field, nothing else
    { type: 'http.switchModel', requestId: 10, session: 'g', creature: 'root', readyId: 7 },
    { type: 'http.switchModel', requestId: 11, session: 'g', creature: 'root', model: '', readyId: 7 },
    { type: 'http.switchModel', requestId: 12, session: 'g', creature: '', model: 'x', readyId: 7 },
    { type: 'http.switchModel', requestId: 13, session: 'g', creature: 'root', model: 'x', method: 'POST', readyId: 7 },
    // instance metadata is session-scoped, read-only
    { type: 'http.instanceMetadata', requestId: 14, readyId: 7 },
    { type: 'http.instanceMetadata', requestId: 15, session: '', readyId: 7 },
    { type: 'http.instanceMetadata', requestId: 16, session: 'g', creature: 'root', readyId: 7 },
    // every model envelope must carry a live ready epoch for ingress admission
    { type: 'http.modelDirectory', requestId: 17 },
    { type: 'http.commandInventory', requestId: 18, session: 'g', creature: 'root' },
    { type: 'http.switchModel', requestId: 19, session: 'g', creature: 'root', model: 'x' },
    { type: 'http.instanceMetadata', requestId: 20, session: 'g' },
    { type: 'http.modelDirectory', requestId: 21, readyId: 0 },
    { type: 'http.commandInventory', requestId: 22, session: 'g', creature: 'root', readyId: '7' },
  ]
  for (const message of invalid) assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('Host client builds the exact encoded fixed route for each model/command/instance operation', async () => {
  const calls = []
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: 'host-secret',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => ({}) }
    },
  })

  await client.modelDirectory()
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/configs/models')
  assert.equal(calls[0].options.method, undefined)
  assert.equal(calls[0].options.headers['X-KT-Host-Token'], 'host-secret')
  assert.equal(calls[0].options.redirect, 'error')

  calls.length = 0
  await client.commandInventory('sess one', 'root/x y')
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/sess%20one/creatures/root%2Fx%20y/command-inventory')
  assert.equal(calls[0].options.method, undefined)

  calls.length = 0
  await client.switchModel('sess one', 'root/x y', 'openai/gpt-5@effort=high')
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/sess%20one/creatures/root%2Fx%20y/model')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), { model: 'openai/gpt-5@effort=high' })

  calls.length = 0
  await client.instanceMetadata('sess one')
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/active/sess%20one')
  assert.equal(calls[0].options.method, undefined)
})

test('Host client preserves the exact HTTP status for each model/instance failure', async () => {
  const failing = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async (url) => ({ ok: false, status: url.endsWith('/model') ? 400 : 404 }),
  })
  await assert.rejects(failing.switchModel('g', 'root', 'x'), (error) => {
    assert.equal(error.status, 400)
    return true
  })
  await assert.rejects(failing.commandInventory('g', 'root'), (error) => {
    assert.equal(error.status, 404)
    return true
  })
  await assert.rejects(failing.instanceMetadata('g'), (error) => {
    assert.equal(error.status, 404)
    return true
  })
  await assert.rejects(failing.modelDirectory(), (error) => {
    assert.equal(error.status, 404)
    return true
  })
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
    requests.push({ method: req.method, url: req.url, token: req.headers['x-kt-host-token'] })
    if (handler) handler(req, res)
    else json(res, 200, {})
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
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

test('the runtime routes the model directory, live command inventory and instance metadata over fixed GET routes', async () => {
  const srv = await withServer()
  try {
    const MODELS = [
      {
        name: 'gpt-5',
        model: 'gpt-5',
        provider: 'openai',
        available: true,
        variation_groups: { effort: { high: {} } },
        selected_variations: { effort: 'high' },
      },
    ]
    const INVENTORY = { commands: [{ name: 'goal', aliases: [], description: 'goal' }], skills: [] }
    const INSTANCE = { session_id: 'graph_1', type: 'terrarium', creatures: [{ creature_id: 'c1', name: 'root/x y' }] }
    srv.setHandler((req, res) => {
      if (req.url === '/api/configs/models') return json(res, 200, MODELS)
      if (req.url === '/api/sessions/graph_1/creatures/root%2Fx%20y/command-inventory') return json(res, 200, INVENTORY)
      if (req.url === '/api/sessions/active/graph_1') return json(res, 200, INSTANCE)
      return json(res, 404, { detail: 'unknown' })
    })
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({ type: 'http.modelDirectory', requestId: 1, readyId: 100 })
    await host.handle({ type: 'http.commandInventory', requestId: 2, session: 'graph_1', creature: 'root/x y', readyId: 100 })
    await host.handle({ type: 'http.instanceMetadata', requestId: 3, session: 'graph_1', readyId: 100 })

    assert.deepEqual(
      srv.requests.map((request) => request.url),
      ['/api/configs/models', '/api/sessions/graph_1/creatures/root%2Fx%20y/command-inventory', '/api/sessions/active/graph_1'],
    )
    assert.deepEqual(
      srv.requests.map((request) => request.method),
      ['GET', 'GET', 'GET'],
    )
    for (const request of srv.requests) assert.equal(request.token, 'host-secret')
    assert.deepEqual(posts, [
      { type: 'http.modelDirectory.result', requestId: 1, data: MODELS },
      { type: 'http.commandInventory.result', requestId: 2, data: INVENTORY },
      { type: 'http.instanceMetadata.result', requestId: 3, data: INSTANCE },
    ])
  } finally {
    await srv.close()
  }
})

test('the runtime switches the target model over the fixed POST route and forwards the canonical selector', async () => {
  const srv = await withServer()
  try {
    let body = null
    srv.setHandler((req, res) => {
      let raw = ''
      req.on('data', (chunk) => (raw += chunk))
      req.on('end', () => {
        body = raw ? JSON.parse(raw) : null
        json(res, 200, { status: 'switched', model: 'openai/gpt-5@effort=high' })
      })
    })
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({
      type: 'http.switchModel',
      requestId: 4,
      session: 'graph_1',
      creature: 'root/x y',
      model: 'openai/gpt-5@effort=high',
      readyId: 100,
    })

    assert.equal(srv.requests[0].method, 'POST')
    assert.equal(srv.requests[0].url, '/api/sessions/graph_1/creatures/root%2Fx%20y/model')
    assert.deepEqual(body, { model: 'openai/gpt-5@effort=high' })
    assert.deepEqual(posts, [
      { type: 'http.switchModel.result', requestId: 4, data: { status: 'switched', model: 'openai/gpt-5@effort=high' } },
    ])
  } finally {
    await srv.close()
  }
})

test('a refused switch forwards the exact 400 and posts no result (no blind retry)', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 400, { detail: 'unknown model' }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await assert.rejects(
      host.handle({ type: 'http.switchModel', requestId: 5, session: 'graph_1', creature: 'root/x y', model: 'nope', readyId: 100 }),
      (error) => {
        assert.equal(error.status, 400)
        return true
      },
    )
    assert.equal(srv.requests.length, 1, 'a mutation is never retried')
    assert.deepEqual(posts, [])
  } finally {
    await srv.close()
  }
})

test('a malformed envelope or a stale selection is rejected before any network side effect', async () => {
  const srv = await withServer()
  try {
    const { host, state } = makeRuntime(srv.origin)
    state.selection = selection()

    for (const message of [
      { type: 'http.switchModel', requestId: 6, session: 'graph_1', creature: 'root/x y', readyId: 100 },
      { type: 'http.switchModel', requestId: 7, session: 'graph_1', creature: 'root/x y', model: 'x', url: '/y', readyId: 100 },
      { type: 'http.commandInventory', requestId: 8, session: 'graph_1', readyId: 100 },
      { type: 'http.instanceMetadata', requestId: 9, session: 'graph_1', creature: 'root/x y', readyId: 100 },
    ]) {
      await assert.rejects(host.handle(message), /Invalid/)
    }
    assert.equal(srv.requests.length, 0, 'no malformed request reached the network')

    host.state.selection = null
    await assert.rejects(
      host.handle({ type: 'http.commandInventory', requestId: 10, session: 'graph_1', creature: 'root/x y', readyId: 100 }),
      /ownership changed/,
    )
    await assert.rejects(
      host.handle({ type: 'http.switchModel', requestId: 11, session: 'graph_1', creature: 'root/x y', model: 'x', readyId: 100 }),
      /ownership changed/,
    )
    await assert.rejects(
      host.handle({ type: 'http.instanceMetadata', requestId: 12, session: 'graph_1', readyId: 100 }),
      /ownership changed/,
    )
    assert.equal(srv.requests.length, 0, 'a stale target never reaches the network')
  } finally {
    await srv.close()
  }
})

test('a real target change or ready reset suppresses an in-flight model read, while an unchanged-topology refresh does not', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, { commands: [], skills: [] })))

    const first = makeRuntime(srv.origin)
    first.state.selection = selection()
    const changed = first.host.handle({
      type: 'http.commandInventory',
      requestId: 13,
      session: 'graph_1',
      creature: 'root/x y',
      readyId: 100,
    })
    await srv.waitForRequest(1)
    // Actual target change: a new selection object + explicit-intent bump.
    first.state.selection = { session: 'graph_1', creature: 'root/x y', targetCreatureId: 'c2' }
    first.host.selectionIntentVersion++
    srv.releases.shift()()
    await assert.rejects(changed, /ownership changed/)
    assert.deepEqual(first.posts, [])

    const second = makeRuntime(srv.origin)
    second.state.selection = selection()
    const stable = second.host.handle({
      type: 'http.commandInventory',
      requestId: 14,
      session: 'graph_1',
      creature: 'root/x y',
      readyId: 100,
    })
    await srv.waitForRequest(2)
    // Unchanged-target topology refresh: selectionVersion advances, object + intent stay.
    second.host.selectionVersion++
    srv.releases.shift()()
    await stable
    assert.deepEqual(
      second.posts.map((post) => post.type),
      ['http.commandInventory.result'],
    )

    const third = makeRuntime(srv.origin)
    third.state.selection = selection()
    const reset = third.host.handle({
      type: 'http.commandInventory',
      requestId: 15,
      session: 'graph_1',
      creature: 'root/x y',
      readyId: 100,
    })
    await srv.waitForRequest(3)
    // Ready reset: a new runtime epoch supersedes the captured readyId.
    third.host.runtimeEpoch = 200
    srv.releases.shift()()
    await assert.rejects(reset, /ownership changed/)
    assert.deepEqual(third.posts, [])
  } finally {
    await srv.close()
  }
})

test('a request emitted under the old ready epoch is rejected before any network side effect', async () => {
  const srv = await withServer()
  try {
    const { host, state } = makeRuntime(srv.origin, 100)
    state.selection = selection()
    host.beginReady(200)

    for (const message of [
      { type: 'http.modelDirectory', requestId: 30, readyId: 100 },
      { type: 'http.commandInventory', requestId: 31, session: 'graph_1', creature: 'root/x y', readyId: 100 },
      { type: 'http.instanceMetadata', requestId: 32, session: 'graph_1', readyId: 100 },
      { type: 'http.switchModel', requestId: 33, session: 'graph_1', creature: 'root/x y', model: 'x', readyId: 100 },
    ]) {
      await assert.rejects(host.handle(message), /Ready ownership changed/)
    }
    assert.equal(srv.requests.length, 0, 'an old-ready request never reaches the network')
  } finally {
    await srv.close()
  }
})

test('a queued model switch posts nothing when the ready epoch or selection intent changes before dequeue', async () => {
  const srv = await withServer()
  try {
    const supersede = [(host) => host.beginReady(200), (host) => host.selectionIntentVersion++]
    for (const change of supersede) {
      const { host, state } = makeRuntime(srv.origin, 100)
      state.selection = selection()
      let release
      host.selectionOperationTail = new Promise((resolve) => (release = resolve))
      const switching = host.handle({
        type: 'http.switchModel',
        requestId: 34,
        session: 'graph_1',
        creature: 'root/x y',
        model: 'x',
        readyId: 100,
      })
      change(host)
      release()
      await assert.rejects(switching, /ownership changed/)
    }
    assert.equal(srv.requests.length, 0, 'a switch queued under a superseded ready or intent never POSTs')
  } finally {
    await srv.close()
  }
})

test('a started switch POST is neither retried nor reported as success after supersession', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, { status: 'switched', model: 'x' })))
    const { host, state, posts } = makeRuntime(srv.origin, 100)
    state.selection = selection()
    const switching = host.handle({
      type: 'http.switchModel',
      requestId: 35,
      session: 'graph_1',
      creature: 'root/x y',
      model: 'x',
      readyId: 100,
    })
    await srv.waitForRequest(1)
    host.beginReady(200)
    srv.releases.shift()()
    await assert.rejects(switching, /ownership changed/)
    assert.equal(srv.requests.length, 1, 'a started mutation is never retried')
    assert.deepEqual(posts, [], 'a superseded switch is not reported as success')
  } finally {
    await srv.close()
  }
})

test('the named model facade installs the delegates, presents status, and revokes them on dispose', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  const { installModelBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'modelBridge.mjs')))

  const calls = []
  let failure = null
  const request = async (type, data) => {
    calls.push({ type, data })
    if (failure) throw failure
    return { models: [], commands: [], skills: [] }
  }

  const uninstall = installModelBridge({ request, getOwner: () => ({ readyId: 100 }) })
  try {
    await shim.configAPI.getModels()
    assert.deepEqual(calls.at(-1), { type: 'http.modelDirectory', data: { readyId: 100 } })

    await shim.terrariumAPI.getCreatureCommandInventory('g', 'root')
    assert.deepEqual(calls.at(-1), { type: 'http.commandInventory', data: { session: 'g', creature: 'root', readyId: 100 } })

    await shim.terrariumAPI.switchCreatureModel('g', 'root', 'openai/gpt-5@effort=high')
    assert.deepEqual(calls.at(-1), {
      type: 'http.switchModel',
      data: { session: 'g', creature: 'root', model: 'openai/gpt-5@effort=high', readyId: 100 },
    })

    await shim.sessionAPI.getActive('g')
    assert.deepEqual(calls.at(-1), { type: 'http.instanceMetadata', data: { session: 'g', readyId: 100 } })

    // A refused switch must reach the leaf with the Host's safe status preserved.
    const conflict = Error('switch_failed')
    conflict.status = 400
    failure = conflict
    await assert.rejects(shim.terrariumAPI.switchCreatureModel('g', 'root', 'x'), (error) => {
      assert.equal(error.response.status, 400)
      assert.equal(error.status, 400)
      return true
    })
  } finally {
    uninstall()
  }

  for (const name of ['__ktVsCodeModelDirectory', '__ktVsCodeCommandInventory', '__ktVsCodeSwitchModel', '__ktVsCodeInstanceMetadata']) {
    assert.equal(globalThis[name], undefined, name)
  }

  // A missing delegate rejects explicitly instead of silently resolving.
  await assert.rejects(shim.terrariumAPI.switchCreatureModel('g', 'root', 'x'), /bridge is unavailable/)
  await assert.rejects(shim.configAPI.getModels(), /bridge is unavailable/)
  await assert.rejects(shim.sessionAPI.getActive('g'), /bridge is unavailable/)
})

test('the model facade is fenced by the ready owner and refuses a captured delegate after dispose', async () => {
  const { installModelBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'modelBridge.mjs')))

  const calls = []
  let owner = { readyId: null }
  let settle = null
  const request = (type, data) => {
    calls.push({ type, data })
    return new Promise((resolve) => (settle = resolve))
  }

  const uninstall = installModelBridge({ request, getOwner: () => owner })
  const captured = globalThis.__ktVsCodeSwitchModel
  try {
    // Not ready: the owner has no live epoch, so no request is emitted.
    await assert.rejects(globalThis.__ktVsCodeCommandInventory('g', 'root'), /Wait for Session refresh/)
    assert.deepEqual(calls, [])

    owner = { readyId: 42 }
    const sent = captured('g', 'root', 'x')
    assert.deepEqual(calls.at(-1), {
      type: 'http.switchModel',
      data: { session: 'g', creature: 'root', model: 'x', readyId: 42 },
    })

    // A ready reset before settlement rejects instead of reporting a false success.
    owner = { readyId: 43 }
    settle({ status: 'switched' })
    await assert.rejects(sent, /ownership changed/)
  } finally {
    uninstall()
  }

  // A captured delegate reference must not dispatch once the facade is revoked.
  const before = calls.length
  await assert.rejects(captured('g', 'root', 'x'), /disposed/)
  assert.equal(calls.length, before, 'a disposed facade emits no request')
})

test('the command inventory does not widen executeCreatureCommand beyond goal-only dispatch', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  // Real model/slash commands still cannot be dispatched: inventory is not authorization.
  await assert.rejects(shim.terrariumAPI.executeCreatureCommand('g', 'root', 'model', 'openai/gpt-5'), /Only goal commands are supported/)
  await assert.rejects(shim.terrariumAPI.executeCreatureCommand('g', 'root', 'clear', '--force'), /Only goal commands are supported/)
  assert.equal(typeof shim.sessionAPI.getActive, 'function')
  assert.equal(typeof shim.configAPI.getModels, 'function')
})
