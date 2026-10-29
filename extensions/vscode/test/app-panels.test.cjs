// The Host side of editor-tab apps: message validation, the fixed outer panel
// policy, relaying only bridge-shaped messages, and asking the user before a
// kt.send reaches the agent.
const assert = require('node:assert/strict')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { AppPanels, PANEL_POLICY, bridgeMessage, renderAppPanelHtml } = require('../src/host/appPanels.cjs')

function fakePanel(title) {
  const panel = {
    title,
    revealed: 0,
    disposed: false,
    sent: [],
    listeners: { message: [], dispose: [] },
    webview: {
      html: '',
      postMessage: async (message) => panel.sent.push(message),
      onDidReceiveMessage: (fn) => (panel.listeners.message.push(fn), { dispose() {} }),
    },
    onDidDispose: (fn) => (panel.listeners.dispose.push(fn), { dispose() {} }),
    reveal: () => (panel.revealed += 1),
    dispose: () => {
      if (panel.disposed) return
      panel.disposed = true
      for (const fn of panel.listeners.dispose) fn()
    },
    fromApp: (message) => Promise.all(panel.listeners.message.map((fn) => fn(message))),
  }
  return panel
}

function setup(answers = []) {
  const panels = []
  const posted = []
  const asked = []
  const apps = new AppPanels({
    createPanel: (title) => {
      const panel = fakePanel(title)
      panels.push(panel)
      return panel
    },
    confirmSend: async (question) => (asked.push(question), answers.shift() ?? null),
    post: (message) => posted.push(message),
  })
  return { apps, panels, posted, asked }
}

test('app requests are a closed set of fields with bounded content', () => {
  const ok = (message) => allowedMessage({ requestId: 1, ...message })
  assert.equal(ok({ type: 'app.open', appId: 'w:w_1', title: 'A', html: '<p>x</p>' }), true)
  assert.equal(ok({ type: 'app.open', appId: 'w:w_1', title: 'A', html: '<p>x</p>', url: 'https://x' }), false)
  assert.equal(ok({ type: 'app.open', appId: 'bad id', title: 'A', html: '' }), false)
  assert.equal(ok({ type: 'app.open', appId: 'a', title: 'A', html: 'x'.repeat(4 * 1024 * 1024 + 1) }), false)
  assert.equal(ok({ type: 'app.document', path: '/api/sessions/s1/artifacts/apps/a.html' }), true)
  assert.equal(ok({ type: 'app.document', path: '/api/sessions/s1/artifacts/../../etc/passwd' }), false)
  assert.equal(ok({ type: 'app.document', path: 'https://evil.test/a.html' }), false)
  assert.equal(ok({ type: 'app.post', appId: 'a', message: { jsonrpc: '2.0', id: 1, result: {} } }), true)
  assert.equal(ok({ type: 'app.post', appId: 'a', message: { id: 1 } }), false, 'not JSON-RPC')
  assert.equal(ok({ type: 'app.close', appId: 'a' }), true)
  assert.equal(allowedMessage({ type: 'app.close', appId: 'a' }), false, 'a request id is required')
})

test('the panel page puts the fixed outer policy and the transport before the app document', () => {
  const html = renderAppPanelHtml('<meta http-equiv="Content-Security-Policy" content="inner"><p>app</p>')
  assert.ok(html.startsWith(`<meta http-equiv="Content-Security-Policy" content="${PANEL_POLICY}">`))
  for (const directive of ["connect-src 'none'", "frame-src 'none'", "form-action 'none'", "worker-src 'none'"])
    assert.ok(PANEL_POLICY.split('; ').includes(directive), directive)
  assert.doesNotMatch(PANEL_POLICY, /vscode|file:|'self'/, 'no local resources')
  const prelude = html.indexOf('acquireVsCodeApi()')
  assert.ok(prelude > 0 && prelude < html.indexOf('content="inner"'), 'the transport runs before the app')
  assert.ok(html.endsWith('<p>app</p>'))
})

test('bridge messages are JSON-RPC only, copied, and bounded', () => {
  assert.equal(bridgeMessage({ jsonrpc: '2.0', method: 'kt/resize', params: { height: 1 } }).method, 'kt/resize')
  assert.equal(bridgeMessage({ jsonrpc: '1.0' }), null)
  assert.equal(bridgeMessage({ jsonrpc: '2.0', method: 'bad method!' }), null)
  assert.equal(bridgeMessage({ jsonrpc: '2.0', method: 'x', params: { big: 'x'.repeat(1024 * 1024) } }), null)
  assert.equal(bridgeMessage([1]), null)
  const original = { jsonrpc: '2.0', params: { a: 1 } }
  const copy = bridgeMessage(original)
  assert.notEqual(copy, original)
  assert.deepEqual(copy, original)
})

test('open, relay, reopen and close an app tab', async () => {
  const { apps, panels, posted } = setup()
  assert.deepEqual(await apps.handle({ type: 'app.open', appId: 'w:1', title: 'Explorer', html: '<p>v1</p>' }), { opened: true })
  assert.equal(panels.length, 1)
  assert.ok(panels[0].webview.html.endsWith('<p>v1</p>'))

  await panels[0].fromApp({ jsonrpc: '2.0', id: 1, method: 'ui/initialize', params: {} })
  await panels[0].fromApp({ not: 'json-rpc' })
  assert.deepEqual(posted, [{ type: 'app.message', appId: 'w:1', message: { jsonrpc: '2.0', id: 1, method: 'ui/initialize', params: {} } }])

  await apps.handle({ type: 'app.post', appId: 'w:1', message: { jsonrpc: '2.0', id: 1, result: { ok: true } } })
  assert.deepEqual(panels[0].sent.at(-1), { jsonrpc: '2.0', id: 1, result: { ok: true } })

  // The same app again reveals its tab; a changed document replaces the page.
  await apps.handle({ type: 'app.open', appId: 'w:1', title: 'Explorer 2', html: '<p>v1</p>' })
  await apps.handle({ type: 'app.open', appId: 'w:1', title: 'Explorer 2', html: '<p>v2</p>' })
  assert.equal(panels.length, 1)
  assert.equal(panels[0].revealed, 2)
  assert.equal(panels[0].title, 'Explorer 2')
  assert.ok(panels[0].webview.html.endsWith('<p>v2</p>'))

  await apps.handle({ type: 'app.close', appId: 'w:1' })
  assert.equal(panels[0].disposed, true)
  assert.deepEqual(posted.at(-1), { type: 'app.closed', appId: 'w:1' })
  await assert.rejects(apps.handle({ type: 'app.post', appId: 'w:1', message: { jsonrpc: '2.0', id: 2 } }), /closed/)
  await panels[0].fromApp({ jsonrpc: '2.0', id: 3, method: 'kt/resize' })
  assert.equal(posted.length, 2, 'a closed tab relays nothing')
})

test('a kt.send reaches the chat only after the user says yes', async () => {
  const { apps, panels, posted, asked } = setup([null, 'send', 'always'])
  await apps.handle({ type: 'app.open', appId: 'w:1', title: 'Explorer', html: '' })
  const send = (id) => panels[0].fromApp({ jsonrpc: '2.0', id, method: 'kt/send', params: { name: 'pick', data: {} } })

  await send(1)
  assert.deepEqual(asked, [{ title: 'Explorer', name: 'pick' }])
  assert.equal(posted.length, 0)
  assert.deepEqual(panels[0].sent.at(-1), { jsonrpc: '2.0', id: 1, error: { message: 'the user did not send it' } })

  await send(2)
  assert.deepEqual(posted.at(-1), {
    type: 'app.message',
    appId: 'w:1',
    message: { jsonrpc: '2.0', id: 2, method: 'kt/send', params: { name: 'pick', data: {} } },
    interacted: true,
  })
  await send(3)
  await send(4)
  assert.equal(asked.length, 3, '"Always for this app" stops asking')
  assert.equal(posted.filter((m) => m.interacted).length, 3)
})

test('documents are read only by a canonical artifact route, and disposing closes every tab', async () => {
  const { apps, panels } = setup()
  const reads = []
  const runtime = { client: { artifactText: async (route, max) => (reads.push([route, max]), '<p>doc</p>') } }
  assert.deepEqual(await apps.handle({ type: 'app.document', path: '/api/sessions/s1/artifacts/apps/a.html' }, runtime), {
    html: '<p>doc</p>',
  })
  assert.deepEqual(reads, [['/api/sessions/s1/artifacts/apps/a.html', 4 * 1024 * 1024]])
  await assert.rejects(apps.handle({ type: 'app.document', path: '/api/configs/models' }, runtime), /Unknown app document/)
  await assert.rejects(apps.handle({ type: 'app.document', path: '/api/sessions/s1/artifacts/a.html' }, null), /Unknown app document/)
  await apps.handle({ type: 'app.open', appId: 'a', title: 'A', html: '' })
  await apps.handle({ type: 'app.open', appId: 'b', title: 'B', html: '' })
  apps.dispose()
  assert.deepEqual(
    panels.map((p) => p.disposed),
    [true, true],
  )
})

test('the client reads an app document as text and refuses one over the limit', async () => {
  const { createClient } = require('../src/host/client.cjs')
  const calls = []
  const body = (text, length) => ({
    ok: true,
    status: 200,
    headers: { get: (name) => (name === 'content-length' && length != null ? String(length) : null) },
    text: async () => text,
  })
  const responses = [body('<p>doc</p>'), body('', 99), body('x'.repeat(20))]
  const client = createClient({
    endpoint: 'http://127.0.0.1:8001',
    token: 'tok',
    fetchImpl: async (url, options) => (calls.push([url, options.headers['X-KT-Host-Token'], options.redirect]), responses.shift()),
  })
  assert.equal(await client.artifactText('/api/sessions/s/artifacts/a.html', 16), '<p>doc</p>')
  assert.deepEqual(calls[0], ['http://127.0.0.1:8001/api/sessions/s/artifacts/a.html', 'tok', 'error'])
  await assert.rejects(client.artifactText('/api/sessions/s/artifacts/a.html', 16), /too large/, 'declared length')
  await assert.rejects(client.artifactText('/api/sessions/s/artifacts/a.html', 16), /too large/, 'actual length')
})
