const assert = require('node:assert/strict')
const test = require('node:test')

const { resolveLocalConnection } = require('../src/host/connection.cjs')

const bypass = {
  endpoint: 'http://127.0.0.1:8001',
  source: 'daemon',
  requiresToken: false,
  capabilities: { schema: 1 },
}

const strict = {
  ...bypass,
  requiresToken: true,
}

test('loopback-bypass discovery connects without reading or prompting for a token', async () => {
  let secretReads = 0
  let prompts = 0
  const result = await resolveLocalConnection({
    discover: async () => bypass,
    getStoredToken: async () => {
      secretReads++
      return 'unused'
    },
    promptToken: async () => {
      prompts++
      return 'unused'
    },
    verify: async ({ token }) => assert.equal(token, ''),
  })

  assert.equal(result.token, '')
  assert.equal(secretReads, 0)
  assert.equal(prompts, 0)
})

test('strict local discovery silently reuses a valid SecretStorage token', async () => {
  let prompts = 0
  const result = await resolveLocalConnection({
    discover: async () => strict,
    getStoredToken: async () => 'stored-secret',
    promptToken: async () => {
      prompts++
      return 'prompted'
    },
    verify: async ({ token }) => assert.equal(token, 'stored-secret'),
  })

  assert.equal(result.token, 'stored-secret')
  assert.equal(prompts, 0)
})

test('strict local discovery replaces an invalid stored token with one prompt', async () => {
  const stored = []
  const verified = []
  const result = await resolveLocalConnection({
    discover: async () => strict,
    getStoredToken: async () => 'expired-secret',
    promptToken: async () => 'replacement-secret',
    storeToken: async (token) => stored.push(token),
    verify: async ({ token }) => {
      verified.push(token)
      if (token === 'expired-secret') {
        const error = Error('unauthorized')
        error.status = 401
        throw error
      }
    },
  })

  assert.equal(result.token, 'replacement-secret')
  assert.deepEqual(verified, ['expired-secret', 'replacement-secret'])
  assert.deepEqual(stored, ['replacement-secret'])
})

test('strict local discovery does not prompt when a stored-token request fails transiently', async () => {
  let prompts = 0
  await assert.rejects(
    resolveLocalConnection({
      discover: async () => strict,
      getStoredToken: async () => 'stored-secret',
      promptToken: async () => {
        prompts++
        return 'replacement'
      },
      verify: async () => {
        throw Error('connection refused')
      },
    }),
    /connection refused/,
  )
  assert.equal(prompts, 0)
})

test('strict local discovery prompts only for a missing token and stores it after verification', async () => {
  const stored = []
  const result = await resolveLocalConnection({
    discover: async () => strict,
    getStoredToken: async () => '',
    promptToken: async () => 'prompted-secret',
    storeToken: async (token) => stored.push(token),
    verify: async ({ token }) => assert.equal(token, 'prompted-secret'),
  })

  assert.equal(result.token, 'prompted-secret')
  assert.deepEqual(stored, ['prompted-secret'])
})

test('connection verification times out and aborts without saving or replacing a token', async () => {
  let signal
  let prompts = 0
  let saved = 0
  let finish
  const late = new Promise((resolve) => {
    finish = resolve
  })
  const options = {
    discover: async () => strict,
    getStoredToken: async () => 'stored-secret',
    promptToken: async () => {
      prompts++
      return 'replacement'
    },
    storeToken: async () => saved++,
    timeoutMs: 5,
    verify: (_connection, options) => {
      signal = options.signal
      return late
    },
  }
  await assert.rejects(resolveLocalConnection(options), /timed out/)
  assert.equal(signal.aborted, true)
  assert.equal(prompts, 0)
  assert.equal(saved, 0)
  finish()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(saved, 0)
})

test('a rejected or invalid strict token does not create a connection', async () => {
  await assert.rejects(
    resolveLocalConnection({
      discover: async () => strict,
      getStoredToken: async () => '',
      promptToken: async () => undefined,
      verify: async () => {},
    }),
    /Host token is required by the local service/,
  )
})
