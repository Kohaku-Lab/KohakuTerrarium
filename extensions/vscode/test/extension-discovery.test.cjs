const assert = require('node:assert/strict')
const http = require('node:http')
const Module = require('node:module')
const test = require('node:test')

const { discoverLocalKt } = require('../src/host/discovery.cjs')
const localDiscovery = require('../src/host/localDiscovery.cjs')

const strictCapabilities = {
  schema: 1,
  auth: {
    host_token: { enabled: true, loopback_bypass: false },
    admin_token: { enabled: false },
    multi_user: { enabled: false },
  },
}

async function serverFixture(t) {
  const requests = []
  const policy = { diagnostics: 'kt', multiUser: false }
  const server = http.createServer((request, response) => {
    requests.push({ path: request.url, token: request.headers['x-kt-host-token'] || '' })
    let body
    let status = 200
    if (request.url === '/api/auth/capabilities') {
      body = { ...strictCapabilities, auth: { ...strictCapabilities.auth, multi_user: { enabled: policy.multiUser } } }
    } else if (request.headers['x-kt-host-token'] !== 'correct') {
      status = 401
      body = { detail: 'Unauthorized' }
    } else if (request.url === '/api/catalog/server-info/diagnostics') {
      if (policy.diagnostics === 'unavailable') status = 503
      body = policy.diagnostics === 'kt' ? { version: '2', daemon: { pid: 42, mode: 'standalone' } } : { service: 'other' }
    } else if (request.url === '/api/sessions/open') {
      body = { sessions: [] }
    } else {
      status = 404
      body = {}
    }
    response.writeHead(status, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(body))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  })
  return { requests, policy, port: server.address().port }
}

function loadExtension(vscode, discoverInstalledKt) {
  const filename = require.resolve('../src/extension.cjs')
  const originalLoad = Module._load
  delete require.cache[filename]
  Module._load = function (request, parent, isMain) {
    if (request === 'vscode') return vscode
    if (request === './host/localDiscovery.cjs') return { ...localDiscovery, discoverInstalledKt }
    return originalLoad(request, parent, isMain)
  }
  try {
    return require(filename)
  } finally {
    Module._load = originalLoad
    delete require.cache[filename]
  }
}

test('strict fallback authenticates diagnostics before connecting or storing credentials', async (t) => {
  const { requests, policy, port } = await serverFixture(t)
  let state = null
  const discover = (options) =>
    discoverLocalKt({
      ...options,
      readState: async () => state,
      isPidAlive: () => false,
      ports: [port],
      probe: localDiscovery.probeCapabilities,
      verifyProbe: localDiscovery.verifyKtProbe,
    })
  let prompts = 0
  let secret = ''
  let approved = true
  let selections = 0
  let secretReads = 0
  const saved = []
  const context = {
    secrets: {
      get: async () => {
        secretReads++
        return secret
      },
      store: async (_key, token) => saved.push(token),
    },
  }
  const extension = loadExtension(
    {
      window: {
        showQuickPick: async (items, options) => {
          selections++
          assert.match(options.placeHolder, /token/i)
          assert.equal(items[0].label, `http://127.0.0.1:${port}`)
          return approved ? items[0] : undefined
        },
        showInputBox: async (options) => {
          assert.equal(options.password, true)
          prompts++
          return 'correct'
        },
      },
    },
    discover,
  )

  const first = await extension.resolveConnection(context, {})
  assert.equal(first.endpoint, `http://127.0.0.1:${port}`)
  assert.equal(first.source, 'probe')
  assert.equal(first.token, 'correct')
  assert.equal(prompts, 1)
  assert.equal(selections, 1)
  assert.deepEqual(saved, ['correct'])
  assert.deepEqual(requests, [
    { path: '/api/auth/capabilities', token: '' },
    { path: '/api/catalog/server-info/diagnostics', token: 'correct' },
    { path: '/api/sessions/open', token: 'correct' },
  ])

  requests.length = 0
  state = { bound: true, pid: 99, url: 'http://127.0.0.1:8999' }
  secret = 'expired'
  await extension.resolveConnection(context, {})
  assert.equal(prompts, 2)
  assert.deepEqual(
    requests.filter((row) => row.path.endsWith('/diagnostics')).map((row) => row.token),
    ['expired', 'correct'],
  )
  assert.deepEqual(saved, ['correct', 'correct'])

  secret = 'correct'
  await extension.resolveConnection(context, {})
  assert.equal(prompts, 2)
  policy.diagnostics = 'unavailable'
  await assert.rejects(extension.resolveConnection(context, {}))
  assert.equal(prompts, 2, 'transient identity failure must not prompt for a replacement secret')

  requests.length = 0
  policy.diagnostics = 'other'
  secret = ''
  await assert.rejects(extension.resolveConnection(context, {}), /identity/)
  assert.deepEqual(saved, ['correct', 'correct'])
  assert.equal(
    requests.some((row) => row.path === '/api/sessions/open'),
    false,
  )

  policy.diagnostics = 'kt'
  approved = false
  requests.length = 0
  const readsBefore = secretReads
  const promptsBefore = prompts
  await assert.rejects(extension.resolveConnection(context, { endpoint: `http://127.0.0.1:${port}` }), /cancelled/)
  assert.equal(secretReads, readsBefore)
  assert.equal(prompts, promptsBefore)
  assert.equal(
    requests.some((row) => row.token),
    false,
  )

  policy.multiUser = true
  const before = prompts
  await assert.rejects(extension.resolveConnection(context, {}), /No supported local/)
  assert.equal(prompts, before)
})
