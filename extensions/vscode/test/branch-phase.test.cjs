// D1 branch transport phase contract: the exact HTTP status plus the transport
// phase (mayHaveRun / superseded) the shared store guard reads, over a real
// loopback client, and the demux/facade that carry it to the leaf.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const { createClient } = require('../src/host/client.cjs')
const { RuntimeHost } = require('../src/host/runtime.cjs')

const root = path.resolve(__dirname, '..')

function json(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function withServer() {
  const requests = []
  let handler = null
  const server = http.createServer((req, res) => {
    res.on('error', () => {})
    requests.push({ method: req.method, url: req.url })
    if (handler) handler(req, res)
    else json(res, 200, {})
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    requests,
    setHandler(fn) {
      handler = fn
    },
    close() {
      return new Promise((resolve) => server.close(resolve))
    },
  }
}

function makeRuntime(origin) {
  const posts = []
  const host = new RuntimeHost({
    client: createClient({ endpoint: origin, token: 'host-secret', fetchImpl: fetch }),
    state: {
      selection: { session: 'graph_1', creature: 'root/x y', targetCreatureId: 'c1' },
      async updateSelection() {},
      async updateSelectionIf() {
        return true
      },
    },
    sockets: { begin: () => 1, open() {}, send: () => true, closeSocket() {}, closeGeneration() {} },
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt/x',
    getWorkspacePath: () => 'C:/ws',
    socketFactory: () => ({}),
    webSocketBase: 'ws://127.0.0.1:1',
    token: 'host-secret',
    runtimeEpoch: 100,
  })
  return { host, posts }
}

test('a 502 or 504 gateway failure retains the exact status and reports mayHaveRun', async () => {
  const srv = await withServer()
  try {
    for (const code of [502, 504]) {
      srv.setHandler((req, res) => json(res, code, { detail: 'gateway' }))
      const { host, posts } = makeRuntime(srv.origin)
      await assert.rejects(
        host.handle({ type: 'http.regenerate', requestId: code, session: 'graph_1', creature: 'root/x y', readyId: 100 }),
        (error) => {
          assert.equal(error.status, code)
          assert.equal(error.mayHaveRun, true)
          assert.equal(error.superseded, undefined)
          return true
        },
      )
      assert.deepEqual(posts, [], 'no result is painted for a gateway failure')
    }
    assert.equal(srv.requests.length, 2, 'no gateway failure is retried')
  } finally {
    await srv.close()
  }
})

test('the demux carries only the safe status and fixed transport phase to the leaf', async () => {
  const { settleRequestMessage } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'requestDemux.mjs')))
  const pending = new Map()
  const settled = new Promise((resolve, reject) => {
    pending.set(7, { resolve, reject, timer: null, type: 'http.regenerate' })
  })
  assert.equal(
    settleRequestMessage(pending, {
      type: 'error',
      requestId: 7,
      error: 'branch_mutation_failed',
      code: 'branch_mutation_failed',
      status: 409,
      mayHaveRun: true,
      superseded: true,
      token: 'leak',
      endpoint: 'http://x',
    }),
    true,
  )
  const error = await settled.catch((reason) => reason)
  assert.equal(error.status, 409)
  assert.equal(error.mayHaveRun, true)
  assert.equal(error.superseded, true)
  assert.equal(error.token, undefined, 'no token crosses the demux')
  assert.equal(error.endpoint, undefined, 'no raw endpoint crosses the demux')
})

test('the branch facade never invents an HTTP status for a local rejection', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  const { installBranchBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'branchBridge.mjs')))

  let failure = Object.assign(Error('Selected Creature ownership changed'), { mayHaveRun: false })
  const request = async () => {
    throw failure
  }
  const uninstall = installBranchBridge({ request, getOwner: () => ({ admittedReadyId: 100 }) })
  try {
    // A pre-admission supersession has no HTTP status, so no response is fabricated.
    await assert.rejects(shim.agentAPI.regenerate('g', 'root', {}), (error) => {
      assert.equal(error.status, undefined)
      assert.equal(error.response, undefined)
      assert.equal(error.mayHaveRun, false)
      return true
    })

    // A lost response after dispatch likewise carries no invented status.
    failure = Object.assign(Error('network drop'), { mayHaveRun: true })
    await assert.rejects(shim.agentAPI.editMessage('g', 'root', 0, 'x'), (error) => {
      assert.equal(error.status, undefined)
      assert.equal(error.response, undefined)
      assert.equal(error.mayHaveRun, true)
      return true
    })
  } finally {
    uninstall()
  }
})
