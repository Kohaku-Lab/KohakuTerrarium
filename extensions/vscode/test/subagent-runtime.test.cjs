// REAL HTTP loopback suite: drives the RuntimeHost sub-agent/promote adapters
// against a live Node HTTP server and the real global fetch, so the exact wire
// route, the forwarded status, and the target/ready fences are pinned end to end.
// Each server is an isolated ephemeral 127.0.0.1 port closed in teardown.
const assert = require('node:assert/strict')
const http = require('node:http')
const test = require('node:test')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { createClient } = require('../src/host/client.cjs')

const TOKEN = 'host-secret-token'

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
  const { port } = server.address()
  return {
    origin: `http://127.0.0.1:${port}`,
    requests,
    releases,
    setHandler(fn) {
      handler = fn
    },
    // Wait until the Nth request has reached the server, proving the fetch left
    // the process before the test mutates selection/ready.
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

function makeRuntime(origin, runtimeEpoch = 'ready-A') {
  const posts = []
  const updates = []
  const state = {
    selection: null,
    async updateSelection(selection) {
      this.selection = selection
      updates.push(selection)
    },
    async updateSelectionIf(selection, owns) {
      if (!owns()) return false
      this.selection = selection
      updates.push(selection)
      return true
    },
  }
  const host = new RuntimeHost({
    client: createClient({ endpoint: origin, token: TOKEN, fetchImpl: fetch }),
    state,
    sockets: { begin: () => 1, open() {}, send: () => true, closeSocket() {}, closeGeneration() {} },
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt/x',
    getWorkspacePath: () => 'C:/ws',
    socketFactory: () => ({}),
    webSocketBase: 'ws://127.0.0.1:1',
    token: TOKEN,
    runtimeEpoch,
  })
  return { host, state, posts, updates }
}

function selection() {
  return { session: 'graph_1', creature: 'root/x y', targetCreatureId: 'c1' }
}

test('live sub-agent conversation read keeps the exact encoded route and posts the raw payload', async () => {
  const srv = await withServer()
  try {
    const payload = { messages: [{ role: 'user', content: 'hi' }], can_receive: true }
    srv.setHandler((req, res) => json(res, 200, payload))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({
      type: 'http.subagentConversation',
      requestId: 1,
      session: 'graph_1',
      creature: 'root/x y',
      options: { jobId: 'job a/b', name: 'sub agent', run: 3 },
    })

    assert.equal(srv.requests.length, 1)
    assert.equal(
      srv.requests[0].url,
      '/api/sessions/graph_1/creatures/root%2Fx%20y/subagents/conversation?job_id=job+a%2Fb&name=sub+agent&run=3',
    )
    // The Host token travels only on the Host -> backend hop.
    assert.equal(srv.requests[0].token, TOKEN)
    assert.deepEqual(posts, [{ type: 'http.subagentConversation.result', requestId: 1, data: payload }])
  } finally {
    await srv.close()
  }
})

test('saved sub-agent runs and conversation are read-only GETs with no send route', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 200, { runs: [], messages: [] }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({ type: 'http.subagentList', requestId: 2, session: 'graph_1', options: { parent: 'root/x y', name: 'sub agent' } })
    assert.equal(srv.requests[0].method, 'GET')
    assert.equal(srv.requests[0].url, '/api/sessions/graph_1/subagents?parent=root%2Fx+y&name=sub+agent')

    await host.handle({ type: 'http.subagentSavedConversation', requestId: 3, session: 'graph_1', options: { parent: 'root', run: 1 } })
    assert.equal(srv.requests[1].method, 'GET')
    assert.equal(srv.requests[1].url, '/api/sessions/graph_1/subagents/conversation?parent=root&run=1')

    assert.deepEqual(
      posts.map((post) => post.type),
      ['http.subagentList.result', 'http.subagentSavedConversation.result'],
    )
  } finally {
    await srv.close()
  }
})

test('a send forwards a 409 exactly and posts no result', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 409, { detail: 'not live' }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await assert.rejects(
      host.handle({ type: 'http.subagentSend', requestId: 4, session: 'graph_1', creature: 'root/x y', name: 'sub', content: 'hi' }),
      (error) => {
        assert.equal(error.status, 409)
        return true
      },
    )
    assert.equal(srv.requests[0].method, 'POST')
    assert.equal(srv.requests[0].url, '/api/sessions/graph_1/creatures/root%2Fx%20y/subagents/sub/send')
    assert.deepEqual(posts, [])
  } finally {
    await srv.close()
  }
})

test('promote forwards the exact backend status (200 posts, not_found rejects)', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 200, { ok: true, status: 'promoted' }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection()

    await host.handle({ type: 'http.promote', requestId: 5, session: 'graph_1', creature: 'root/x y', jobId: 'job a/b' })
    assert.equal(srv.requests[0].method, 'POST')
    assert.equal(srv.requests[0].url, '/api/sessions/graph_1/creatures/root%2Fx%20y/promote/job%20a%2Fb')
    assert.deepEqual(posts, [{ type: 'http.promote.result', requestId: 5, data: { ok: true, status: 'promoted' } }])

    srv.setHandler((req, res) => json(res, 404, { detail: 'not_found' }))
    await assert.rejects(
      host.handle({ type: 'http.promote', requestId: 6, session: 'graph_1', creature: 'root/x y', jobId: 'missing' }),
      (error) => {
        assert.equal(error.status, 404)
        return true
      },
    )
    assert.equal(posts.length, 1)
  } finally {
    await srv.close()
  }
})

test('a real target change or a ready reset invalidates an in-flight sub-agent read; an unchanged-topology refresh does not', async () => {
  const srv = await withServer()
  try {
    // Hold every response until the test releases it, so the fence can be mutated
    // after the request is dispatched but before it settles.
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, { messages: [] })))

    const first = makeRuntime(srv.origin)
    first.state.selection = selection()
    const changed = first.host.handle({
      type: 'http.subagentConversation',
      requestId: 7,
      session: 'graph_1',
      creature: 'root/x y',
      options: {},
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
      type: 'http.subagentConversation',
      requestId: 8,
      session: 'graph_1',
      creature: 'root/x y',
      options: {},
    })
    await srv.waitForRequest(2)
    // Unchanged-target topology refresh: selectionVersion advances, object + intent stay.
    second.host.selectionVersion++
    srv.releases.shift()()
    await stable
    assert.deepEqual(
      second.posts.map((post) => post.type),
      ['http.subagentConversation.result'],
    )

    const third = makeRuntime(srv.origin)
    third.state.selection = selection()
    const reset = third.host.handle({
      type: 'http.subagentConversation',
      requestId: 9,
      session: 'graph_1',
      creature: 'root/x y',
      options: {},
    })
    await srv.waitForRequest(3)
    // Ready reset: a new runtime epoch supersedes the captured readyId.
    third.host.runtimeEpoch = 'ready-B'
    srv.releases.shift()()
    await assert.rejects(reset, /ownership changed/)
    assert.deepEqual(third.posts, [])
  } finally {
    await srv.close()
  }
})
