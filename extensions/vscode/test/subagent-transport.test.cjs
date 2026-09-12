// Transport slice for the T sub-agent vertical: the strict protocol surface, the
// fixed-route client builders (percent-encoding + status preservation), and the
// webview bridge that installs the shim delegates and revokes them on dispose.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { createClient } = require('../src/host/client.cjs')

const root = path.resolve(__dirname, '..')

test('strict protocol accepts only well-formed sub-agent/promote messages', () => {
  const valid = [
    { type: 'http.subagentConversation', requestId: 1, session: 'graph-1', creature: 'root', options: {} },
    { type: 'http.subagentConversation', requestId: 2, session: 'graph-1', creature: 'root' },
    {
      type: 'http.subagentConversation',
      requestId: 3,
      session: 'graph-1',
      creature: 'root',
      options: { jobId: 'job-1', name: 'sub', run: 3 },
    },
    { type: 'http.subagentList', requestId: 4, session: 'graph-1' },
    { type: 'http.subagentList', requestId: 5, session: 'graph-1', options: { parent: 'root', name: 'sub' } },
    { type: 'http.subagentSavedConversation', requestId: 6, session: 'graph-1', options: { parent: 'root', name: 'sub', run: 1 } },
    { type: 'http.subagentSend', requestId: 7, session: 'graph-1', creature: 'root', name: 'sub', content: 'hi' },
    { type: 'http.subagentSend', requestId: 8, session: 'graph-1', creature: 'root', name: 'sub', content: 'hi', jobId: 'job-1' },
    { type: 'http.promote', requestId: 9, session: 'graph-1', creature: 'root', jobId: 'job-1' },
  ]
  for (const message of valid) assert.equal(allowedMessage(message), true, JSON.stringify(message))

  const invalid = [
    // live read needs both target identities
    { type: 'http.subagentConversation', requestId: 10, session: 'graph-1', options: {} },
    { type: 'http.subagentConversation', requestId: 11, session: '', creature: 'root', options: {} },
    // malformed option containers / smuggled keys / bad run type
    { type: 'http.subagentConversation', requestId: 12, session: 'g', creature: 'root', options: 'job_id=j' },
    { type: 'http.subagentConversation', requestId: 13, session: 'g', creature: 'root', options: ['jobId'] },
    { type: 'http.subagentConversation', requestId: 14, session: 'g', creature: 'root', options: { url: '/x' } },
    { type: 'http.subagentConversation', requestId: 15, session: 'g', creature: 'root', options: { run: 1.5 } },
    // saved discovery/recovery is session-scoped, read-only, and rejects a creature
    { type: 'http.subagentList', requestId: 16, session: 'graph-1', creature: 'root' },
    { type: 'http.subagentList', requestId: 17, session: 'graph-1', options: { content: 'x' } },
    { type: 'http.subagentSavedConversation', requestId: 18, session: '', options: {} },
    { type: 'http.subagentSavedConversation', requestId: 19, session: 'g', creature: 'root' },
    // a send is a live, creature-scoped mutation with non-empty content
    { type: 'http.subagentSend', requestId: 20, session: 'g', creature: 'root', name: 'sub' },
    { type: 'http.subagentSend', requestId: 21, session: 'g', creature: 'root', name: 'sub', content: '' },
    { type: 'http.subagentSend', requestId: 22, session: 'g', name: 'sub', content: 'hi' },
    { type: 'http.subagentSend', requestId: 23, session: 'g', creature: 'root', name: 'sub', content: 'hi', method: 'POST' },
    // promote carries the exact job id and no smuggling
    { type: 'http.promote', requestId: 24, session: 'g', creature: 'root' },
    { type: 'http.promote', requestId: 25, session: 'g', creature: 'root', jobId: 'job-1', url: '/x' },
    { type: 'http.promote', requestId: 26, session: 'g', creature: 'root', jobId: '' },
  ]
  for (const message of invalid) assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('Host client builds the exact encoded fixed route for each sub-agent/promote operation', async () => {
  const calls = []
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: 'host-secret',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => ({ messages: [], can_receive: true }) }
    },
  })

  await client.subagentConversation('sess one', 'root/x y', { jobId: 'job a/b', name: 'sub agent', run: 3 })
  assert.equal(
    calls[0].url,
    'http://127.0.0.1:8000/api/sessions/sess%20one/creatures/root%2Fx%20y/subagents/conversation?job_id=job+a%2Fb&name=sub+agent&run=3',
  )
  assert.equal(calls[0].options.headers['X-KT-Host-Token'], 'host-secret')
  assert.equal(calls[0].options.redirect, 'error')

  calls.length = 0
  await client.sendSubagentMessage('sess one', 'root/x y', 'sub agent', { content: 'ping', jobId: 'job-1' })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/sess%20one/creatures/root%2Fx%20y/subagents/sub%20agent/send')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), { content: 'ping', job_id: 'job-1' })

  calls.length = 0
  await client.sendSubagentMessage('g', 'root', 'sub', { content: 'ping' })
  assert.deepEqual(JSON.parse(calls[0].options.body), { content: 'ping' })

  calls.length = 0
  await client.listSubagents('graph_1', { parent: 'root/x y', name: 'sub agent' })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/graph_1/subagents?parent=root%2Fx+y&name=sub+agent')
  assert.equal(calls[0].options.method, undefined)

  calls.length = 0
  await client.savedSubagentConversation('graph_1', { parent: 'root', jobId: 'job-1', run: 2 })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/graph_1/subagents/conversation?parent=root&job_id=job-1&run=2')

  calls.length = 0
  await client.promote('graph_1', 'root/x y', 'job a/b')
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/graph_1/creatures/root%2Fx%20y/promote/job%20a%2Fb')
  assert.equal(calls[0].options.method, 'POST')
})

test('Host client drops an absent identifier instead of emitting an empty query value', async () => {
  const calls = []
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => ({}) }
    },
  })
  await client.subagentConversation('g', 'root', {})
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/g/creatures/root/subagents/conversation')
  await client.listSubagents('g', { name: 'sub' })
  assert.equal(calls[1].url, 'http://127.0.0.1:8000/api/sessions/g/subagents?name=sub')
})

test('Host client preserves the exact HTTP status for a sub-agent conflict and a promote failure', async () => {
  const failing = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async () => ({ ok: false, status: 409 }),
  })
  await assert.rejects(failing.sendSubagentMessage('g', 'root', 'sub', { content: 'hi' }), (error) => {
    assert.equal(error.status, 409)
    return true
  })
  await assert.rejects(failing.subagentConversation('g', 'root'), (error) => {
    assert.equal(error.status, 409)
    return true
  })

  const missing = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async () => ({ ok: false, status: 404 }),
  })
  await assert.rejects(missing.promote('g', 'root', 'job-1'), (error) => {
    assert.equal(error.status, 404)
    return true
  })
})

test('the bridge installs the shim delegates, presents status, and revokes them on dispose', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  const { installSubagentBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'subagentBridge.mjs')))

  // The saved surface is read-only: no delegate ever sends to a finished run.
  assert.equal(typeof shim.sessionAPI.sendSubagentMessage, 'undefined')

  const calls = []
  let failure = null
  const request = async (type, data) => {
    calls.push({ type, data })
    if (failure) throw failure
    return { messages: [], runs: [], can_receive: true }
  }

  const uninstall = installSubagentBridge({ request })
  try {
    await shim.terrariumAPI.getSubagentConversation('g', 'root', { jobId: 'job-1', name: 'sub', run: 2 })
    assert.deepEqual(calls.at(-1), {
      type: 'http.subagentConversation',
      data: { session: 'g', creature: 'root', options: { jobId: 'job-1', name: 'sub', run: 2 } },
    })

    await shim.terrariumAPI.sendSubagentMessage('g', 'root', 'sub', 'hi', 'job-1')
    assert.deepEqual(calls.at(-1), {
      type: 'http.subagentSend',
      data: { session: 'g', creature: 'root', name: 'sub', content: 'hi', jobId: 'job-1' },
    })

    await shim.sessionAPI.listSubagents('g', { parent: 'root', name: 'sub' })
    assert.deepEqual(calls.at(-1), { type: 'http.subagentList', data: { session: 'g', options: { parent: 'root', name: 'sub' } } })

    await shim.sessionAPI.getSubagentConversation('g', { parent: 'root', run: 1 })
    assert.deepEqual(calls.at(-1), {
      type: 'http.subagentSavedConversation',
      data: { session: 'g', options: { parent: 'root', run: 1 } },
    })

    await shim.terrariumAPI.promoteCreatureTask('g', 'root', 'job-1')
    assert.deepEqual(calls.at(-1), { type: 'http.promote', data: { session: 'g', creature: 'root', jobId: 'job-1' } })

    // A saved-run conflict must reach the leaf as ``err.response.status`` so the
    // fallback-to-runs branch still fires; the Host's status is preserved.
    const conflict = Error('subagent_failed')
    conflict.status = 409
    failure = conflict
    await assert.rejects(shim.sessionAPI.getSubagentConversation('g', { parent: 'root', run: 1 }), (error) => {
      assert.equal(error.response.status, 409)
      assert.equal(error.status, 409)
      return true
    })
  } finally {
    uninstall()
  }

  for (const name of [
    '__ktVsCodeSubagentConversation',
    '__ktVsCodeSubagentSend',
    '__ktVsCodeSubagentList',
    '__ktVsCodeSavedSubagentConversation',
    '__ktVsCodePromote',
  ]) {
    assert.equal(globalThis[name], undefined, name)
  }

  // A missing delegate rejects explicitly instead of silently resolving.
  await assert.rejects(shim.terrariumAPI.getSubagentConversation('g', 'root'), /Sub-agent bridge is unavailable/)
})
