// Saved sub-agent discovery/recovery are SESSION-scoped reads. The Host's
// selection authority admits them only for the selected session, so a message
// naming a different session must never reach the backend (no misroute), and a
// saved read captured before a REAL session switch is invalidated while an
// unchanged-topology refresh is not. REAL HTTP loopback + global fetch.
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
    requests.push({ method: req.method, url: req.url })
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

function makeRuntime(origin) {
  const posts = []
  const state = {
    selection: null,
    async updateSelection() {},
    async updateSelectionIf() {
      return false
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
    runtimeEpoch: 'ready-A',
  })
  return { host, state, posts }
}

const selection = (session = 'graph_1') => ({ session, creature: 'root/x y', targetCreatureId: 'c1' })

test('saved sub-agent reads are admitted only for the selected session (no misroute)', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => json(res, 200, { runs: [], messages: [] }))
    const { host, state, posts } = makeRuntime(srv.origin)
    state.selection = selection('graph_1')

    // A saved list/saved conversation for a DIFFERENT session is rejected before
    // any backend hop, so the selected target can never be misrouted.
    await assert.rejects(
      host.handle({ type: 'http.subagentList', requestId: 1, session: 'graph_other', options: { parent: 'root' } }),
      /ownership changed/,
    )
    await assert.rejects(
      host.handle({ type: 'http.subagentSavedConversation', requestId: 2, session: 'graph_other', options: { parent: 'root', run: 1 } }),
      /ownership changed/,
    )
    assert.equal(srv.requests.length, 0)
    assert.deepEqual(posts, [])

    // The matching session routes to the selected session's own saved route.
    await host.handle({ type: 'http.subagentList', requestId: 3, session: 'graph_1', options: { parent: 'root/x y', name: 'sub agent' } })
    assert.equal(srv.requests[0].method, 'GET')
    assert.equal(srv.requests[0].url, '/api/sessions/graph_1/subagents?parent=root%2Fx+y&name=sub+agent')
    assert.deepEqual(
      posts.map((post) => post.type),
      ['http.subagentList.result'],
    )
  } finally {
    await srv.close()
  }
})

test('an in-flight saved read is invalidated by a real session switch but not by an unchanged-topology refresh', async () => {
  const srv = await withServer()
  try {
    srv.setHandler((req, res) => srv.releases.push(() => json(res, 200, { runs: [], messages: [] })))

    // Real switch: a new selection object + an explicit-intent bump.
    const switched = makeRuntime(srv.origin)
    switched.state.selection = selection('graph_1')
    const stale = switched.host.handle({
      type: 'http.subagentSavedConversation',
      requestId: 4,
      session: 'graph_1',
      options: { parent: 'root', run: 1 },
    })
    await srv.waitForRequest(1)
    switched.state.selection = selection('graph_2')
    switched.host.selectionIntentVersion++
    srv.releases.shift()()
    await assert.rejects(stale, /ownership changed/)
    assert.deepEqual(switched.posts, [])

    // Unchanged topology: only the notification-ordering version advances.
    const stable = makeRuntime(srv.origin)
    stable.state.selection = selection('graph_1')
    const kept = stable.host.handle({
      type: 'http.subagentList',
      requestId: 5,
      session: 'graph_1',
      options: { parent: 'root' },
    })
    await srv.waitForRequest(2)
    stable.host.selectionVersion++
    srv.releases.shift()()
    await kept
    assert.deepEqual(
      stable.posts.map((post) => post.type),
      ['http.subagentList.result'],
    )
  } finally {
    await srv.close()
  }
})
