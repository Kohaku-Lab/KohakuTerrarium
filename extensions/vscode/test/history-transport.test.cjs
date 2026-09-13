const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { createClient } = require('../src/host/client.cjs')

const root = path.resolve(__dirname, '..')

test('strict protocol accepts only well-formed paged/detail history messages', () => {
  const valid = [
    { type: 'http.historyPage', requestId: 1, session: 'graph-1', creature: 'root', options: {} },
    { type: 'http.historyPage', requestId: 2, session: 'graph-1', creature: 'root', options: { limit: 400 } },
    {
      type: 'http.historyPage',
      requestId: 3,
      session: 'graph-1',
      creature: 'root',
      options: { limit: 50, before: 'c1', history_id: 'h1', stream: 'events' },
    },
    { type: 'http.historyPage', requestId: 4, session: 'graph-1', creature: 'root', options: { after: 'c2' } },
    { type: 'http.historyPage', requestId: 5, session: 'graph-1', creature: 'root' },
    {
      type: 'http.historyDetail',
      requestId: 6,
      session: 'graph-1',
      creature: 'root',
      params: { stream: 'events', ref: 'tok', history_id: 'h1' },
    },
    {
      type: 'http.historyDetail',
      requestId: 7,
      session: 'graph-1',
      creature: 'root',
      params: { stream: 'snapshot', ref: 'tok', history_id: 'h1' },
    },
  ]
  for (const message of valid) assert.equal(allowedMessage(message), true, JSON.stringify(message))

  const invalid = [
    // missing target identity
    { type: 'http.historyPage', requestId: 10, session: 'graph-1', options: {} },
    { type: 'http.historyPage', requestId: 11, session: '', creature: 'root', options: {} },
    // malformed option containers
    { type: 'http.historyPage', requestId: 12, session: 'g', creature: 'root', options: 'limit=5' },
    { type: 'http.historyPage', requestId: 13, session: 'g', creature: 'root', options: ['limit'] },
    // unknown / smuggled option keys
    { type: 'http.historyPage', requestId: 14, session: 'g', creature: 'root', options: { url: '/x' } },
    { type: 'http.historyPage', requestId: 15, session: 'g', creature: 'root', options: { endpoint: 'http://evil' } },
    // bad limit types/values
    { type: 'http.historyPage', requestId: 16, session: 'g', creature: 'root', options: { limit: 0 } },
    { type: 'http.historyPage', requestId: 17, session: 'g', creature: 'root', options: { limit: -3 } },
    { type: 'http.historyPage', requestId: 18, session: 'g', creature: 'root', options: { limit: '400' } },
    { type: 'http.historyPage', requestId: 19, session: 'g', creature: 'root', options: { limit: 1.5 } },
    // both cursors at once, or non-string cursors
    { type: 'http.historyPage', requestId: 20, session: 'g', creature: 'root', options: { before: 'a', after: 'b' } },
    { type: 'http.historyPage', requestId: 21, session: 'g', creature: 'root', options: { before: 5 } },
    // unknown stream
    { type: 'http.historyPage', requestId: 22, session: 'g', creature: 'root', options: { stream: 'secret' } },
    // extra top-level fields
    { type: 'http.historyPage', requestId: 23, session: 'g', creature: 'root', options: {}, method: 'POST' },
    // detail requires a complete opaque token triple
    { type: 'http.historyDetail', requestId: 24, session: 'g', creature: 'root', params: { stream: 'events', ref: 't' } },
    { type: 'http.historyDetail', requestId: 25, session: 'g', creature: 'root', params: { ref: 't', history_id: 'h' } },
    { type: 'http.historyDetail', requestId: 26, session: 'g', creature: 'root', params: { stream: 'events', ref: '', history_id: 'h' } },
    { type: 'http.historyDetail', requestId: 27, session: 'g', creature: 'root' },
    {
      type: 'http.historyDetail',
      requestId: 28,
      session: 'g',
      creature: 'root',
      params: { stream: 'events', ref: 't', history_id: 'h' },
      url: '/x',
    },
  ]
  for (const message of invalid) assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('Host client forwards paged options on the fixed route and returns the raw payload', async () => {
  const calls = []
  const payload = {
    events: [{ event_id: 1 }],
    messages: [],
    history_page: {
      history_id: 'h1',
      stream: 'events',
      before: null,
      after: 'a1',
      has_older: true,
      has_newer: false,
      reset_required: false,
    },
  }
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: 'host-secret',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => payload }
    },
  })

  const out = await client.historyPage('sess one', 'root/x', { limit: 120, before: 'c1', history_id: 'h1', stream: 'events' })
  assert.equal(
    calls[0].url,
    'http://127.0.0.1:8000/api/sessions/sess%20one/creatures/root%2Fx/history?paged=true&limit=120&before=c1&history_id=h1&stream=events',
  )
  assert.equal(calls[0].options.headers['X-KT-Host-Token'], 'host-secret')
  assert.equal(calls[0].options.redirect, 'error')
  assert.equal(out, payload)
  assert.equal(out.history_page.history_id, 'h1')

  calls.length = 0
  await client.historyPage('s', 'root', { limit: 5000 })
  assert.match(calls[0].url, /paged=true&limit=400$/)

  calls.length = 0
  await client.historyPage('s', 'root')
  assert.match(calls[0].url, /paged=true&limit=400$/)
})

test('Host client fetches detail tokens and preserves HTTP error status', async () => {
  const calls = []
  const client = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return { ok: true, status: 200, json: async () => ({ history_page: { history_id: 'h1' }, record: { _history_key: 'k' } }) }
    },
  })

  const detail = await client.historyDetail('s', 'root', { stream: 'events', ref: 'tok', history_id: 'h1' })
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/sessions/s/creatures/root/history/detail?stream=events&ref=tok&history_id=h1')
  assert.equal(detail.record._history_key, 'k')

  const failing = createClient({
    endpoint: 'http://127.0.0.1:8000',
    token: '',
    fetchImpl: async () => ({ ok: false, status: 409 }),
  })
  await assert.rejects(failing.historyDetail('s', 'root', { stream: 'events', ref: 'tok', history_id: 'h1' }), (error) => {
    assert.equal(error.status, 409)
    return true
  })
  await assert.rejects(failing.historyPage('s', 'root'), (error) => {
    assert.equal(error.status, 409)
    return true
  })
})

test('webview bridge forwards shim reads, preserves status, and uninstalls cleanly', async () => {
  const shim = await import(
    'data:text/javascript,' + encodeURIComponent(fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8'))
  )
  const { installHistoryBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'historyBridge.mjs')))

  const calls = []
  let failure = null
  const request = async (type, data) => {
    calls.push({ type, data })
    if (failure) throw failure
    if (type === 'http.historyPage') return { history_page: { history_id: 'h1', reset_required: false }, messages: [], events: [] }
    return { history_page: { history_id: 'h1' }, record: { _history_key: 'k' } }
  }

  const uninstall = installHistoryBridge({ request })
  try {
    const page = await shim.terrariumAPI.getHistoryPage('g', 'root', { limit: 50, before: 'c1' })
    assert.deepEqual(calls.at(-1), {
      type: 'http.historyPage',
      data: { session: 'g', creature: 'root', options: { limit: 50, before: 'c1' } },
    })
    assert.equal(page.history_page.history_id, 'h1')

    const detail = await shim.terrariumAPI.getHistoryDetail('g', 'root', { stream: 'events', ref: 'tok', history_id: 'h1' })
    assert.deepEqual(calls.at(-1), {
      type: 'http.historyDetail',
      data: { session: 'g', creature: 'root', params: { stream: 'events', ref: 'tok', history_id: 'h1' } },
    })
    assert.equal(detail.record._history_key, 'k')

    const conflict = Error('history_failed')
    conflict.status = 409
    failure = conflict
    await assert.rejects(shim.terrariumAPI.getHistoryDetail('g', 'root', { stream: 'events', ref: 'tok', history_id: 'h1' }), (error) => {
      assert.equal(error.status, 409)
      return true
    })
  } finally {
    uninstall()
  }

  for (const name of ['__ktVsCodeHistory', '__ktVsCodeHistoryPage', '__ktVsCodeHistoryDetail', '__ktVsCodeInterrupt']) {
    assert.equal(globalThis[name], undefined, name)
  }
})
