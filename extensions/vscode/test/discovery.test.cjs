const assert = require('node:assert/strict')
const test = require('node:test')

const { discoverLocalKt, normalizeDaemonEndpoint } = require('../src/host/discovery.cjs')

function response(body, ok = true) {
  return { ok, status: ok ? 200 : 404, json: async () => body }
}

const bypassCapabilities = {
  schema: 1,
  auth: {
    host_token: { enabled: true, loopback_bypass: true },
    admin_token: { enabled: false },
    multi_user: { enabled: false },
  },
}

test('daemon discovery uses the published bound port without probing defaults', async () => {
  const calls = []
  const result = await discoverLocalKt({
    readState: async () => ({ pid: 42, bound: true, url: 'http://127.0.0.1:8123' }),
    isPidAlive: () => true,
    probe: async (endpoint) => {
      calls.push(endpoint)
      return bypassCapabilities
    },
  })

  assert.deepEqual(result, {
    endpoint: 'http://127.0.0.1:8123',
    capabilities: bypassCapabilities,
    source: 'daemon',
    requiresToken: false,
  })
  assert.deepEqual(calls, ['http://127.0.0.1:8123'])
})

test('daemon discovery normalizes wildcard binds to local loopback', () => {
  assert.equal(normalizeDaemonEndpoint('http://0.0.0.0:8001'), 'http://127.0.0.1:8001')
  assert.equal(normalizeDaemonEndpoint('http://[::]:8002'), 'http://[::1]:8002')
  assert.throws(() => normalizeDaemonEndpoint('https://example.com:8001'))
  assert.throws(() => normalizeDaemonEndpoint('http://example.com:8001'))
})

test('stale daemon state falls back to a bounded loopback port probe', async () => {
  const calls = []
  const result = await discoverLocalKt({
    readState: async () => ({ pid: 99, bound: true, url: 'http://127.0.0.1:8999' }),
    isPidAlive: () => false,
    ports: [8001, 8002, 8003],
    probe: async (endpoint) => {
      calls.push(endpoint)
      if (endpoint.endsWith(':8002')) return bypassCapabilities
      throw Error('not KT')
    },
  })

  assert.equal(result.endpoint, 'http://127.0.0.1:8002')
  assert.equal(result.source, 'probe')
  assert.deepEqual(calls, ['http://127.0.0.1:8001', 'http://127.0.0.1:8002', 'http://127.0.0.1:8003'])
})

test('bounded probe continues within a batch after a false identity match', async () => {
  const verified = []
  const result = await discoverLocalKt({
    readState: async () => null,
    ports: [8001, 8002],
    probe: async () => bypassCapabilities,
    verifyProbe: async (endpoint) => {
      verified.push(endpoint)
      return endpoint.endsWith(':8002')
    },
  })

  assert.equal(result.endpoint, 'http://127.0.0.1:8002')
  assert.deepEqual(verified, ['http://127.0.0.1:8001', 'http://127.0.0.1:8002'])
})

test('bounded probe defers strict identity verification until credentials are available', async () => {
  const strictCapabilities = {
    schema: 1,
    auth: {
      host_token: { enabled: true, loopback_bypass: false },
      admin_token: { enabled: false },
      multi_user: { enabled: false },
    },
  }
  let diagnostics = 0
  const strict = await discoverLocalKt({
    readState: async () => null,
    ports: [8001],
    probe: async () => strictCapabilities,
    verifyProbe: async () => {
      diagnostics++
      return true
    },
  })
  assert.equal(strict.endpoint, 'http://127.0.0.1:8001')
  assert.equal(strict.requiresToken, true)
  assert.equal(diagnostics, 0)

  await assert.rejects(
    discoverLocalKt({
      readState: async () => null,
      ports: [8001],
      probe: async () => bypassCapabilities,
      verifyProbe: async () => false,
    }),
    /No supported local KohakuTerrarium service was found/,
  )
})

test('bounded probe continues after an identity request times out', async () => {
  const verified = []
  const result = await discoverLocalKt({
    readState: async () => null,
    ports: [8001, 8002],
    probe: async () => bypassCapabilities,
    verifyProbe: async (endpoint) => {
      verified.push(endpoint)
      if (endpoint.endsWith(':8001')) throw Error('body read timed out')
      return true
    },
  })
  assert.equal(result.endpoint, 'http://127.0.0.1:8002')
  assert.deepEqual(verified, ['http://127.0.0.1:8001', 'http://127.0.0.1:8002'])
})

test('strict fallback selection includes later candidates and cannot select an unrelated endpoint', async () => {
  const strictCapabilities = {
    ...bypassCapabilities,
    auth: { ...bypassCapabilities.auth, host_token: { enabled: true, loopback_bypass: false } },
  }
  const options = {
    readState: async () => null,
    ports: [8001, 8002, 8003],
    concurrency: 2,
    probe: async () => strictCapabilities,
  }
  const selected = await discoverLocalKt({
    ...options,
    selectStrictCandidate: async (candidates) => {
      assert.deepEqual(
        candidates.map((candidate) => candidate.endpoint),
        ['http://127.0.0.1:8001', 'http://127.0.0.1:8002', 'http://127.0.0.1:8003'],
      )
      return candidates[2]
    },
  })
  assert.equal(selected.endpoint, 'http://127.0.0.1:8003')
  await assert.rejects(discoverLocalKt({ ...options, selectStrictCandidate: async () => ({ endpoint: 'http://remote:80' }) }))
})

test('strict local auth is discovered without requiring an endpoint prompt', async () => {
  const capabilities = {
    schema: 1,
    auth: {
      host_token: { enabled: true, loopback_bypass: false },
      admin_token: { enabled: false },
      multi_user: { enabled: false },
    },
  }
  const result = await discoverLocalKt({
    readState: async () => ({ pid: 42, bound: true, url: 'http://127.0.0.1:8001' }),
    isPidAlive: () => true,
    probe: async () => capabilities,
  })

  assert.equal(result.endpoint, 'http://127.0.0.1:8001')
  assert.equal(result.requiresToken, true)
})

test('multi-user or non-KT services are never accepted as local targets', async () => {
  await assert.rejects(
    discoverLocalKt({
      readState: async () => null,
      ports: [8001],
      probe: async () => ({
        schema: 1,
        auth: {
          host_token: { enabled: false, loopback_bypass: true },
          admin_token: { enabled: false },
          multi_user: { enabled: true },
        },
      }),
    }),
    /No supported local KohakuTerrarium service was found/,
  )
})
