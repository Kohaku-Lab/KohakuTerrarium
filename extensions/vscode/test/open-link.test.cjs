// Host platform link opener. A user click in the webview hands one raw
// model-authored reference to the narrow ``platform.openLink`` operation; the
// Host resolves it against the live backend base and calls the injected
// ``vscode.env.openExternal``. These pin the refusal surface (scheme, credentials,
// backslashes, protocol-relative), the relative-resolution contract (query/hash
// kept, token never added), and the ready/selection fence — including the
// stale-after-await case where the result is suppressed.
const assert = require('node:assert/strict')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { resolveOpenTarget } = require('../src/host/openLink.cjs')
const { harness } = require('./runtimeHarness.cjs')

const BASE = 'http://127.0.0.1:8000'
const READY = 700

function build({ openExternal = null } = {}) {
  const opened = []
  const { host, posts } = harness({
    backendBase: BASE,
    runtimeEpoch: READY,
    openExternal: openExternal
      ? async (url) => {
          opened.push(url)
          return openExternal(url)
        }
      : async (url) => {
          opened.push(url)
          return true
        },
  })
  return { host, opened, posts }
}

const send = (host, target, overrides = {}) =>
  host.handle({ type: 'platform.openLink', requestId: 41, target, readyId: READY, ...overrides })

test('platform.openLink accepts only its exact envelope', () => {
  assert.equal(allowedMessage({ type: 'platform.openLink', requestId: 41, target: '/docs', readyId: 1 }), true)
  for (const message of [
    { type: 'platform.openLink', requestId: 41, target: '/docs' },
    { type: 'platform.openLink', requestId: 41, readyId: 1 },
    { type: 'platform.openLink', requestId: 41, target: '', readyId: 1 },
    { type: 'platform.openLink', requestId: 41, target: '/docs', readyId: 0 },
    { type: 'platform.openLink', requestId: 41, target: '/docs', readyId: 1, endpoint: 'http://evil' },
  ])
    assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('resolveOpenTarget refuses every unsupported reference', () => {
  for (const target of [
    'javascript:alert(1)',
    'data:text/html,<h1>x</h1>',
    'command:workbench.action.reloadWindow',
    'file:///etc/passwd',
    'blob:https://example.test/abc',
    'mailto:team@example.test',
    'tel:+123456',
    '#section',
    '//evil.test/path',
    'https://user:pass@example.test/docs',
    'http://user@example.test/docs',
    'C:\\Users\\secret',
    '\\\\server\\share',
    '',
    '   ',
    null,
    42,
  ])
    assert.equal(resolveOpenTarget(target, BASE), null, String(target))
})

test('resolveOpenTarget permits absolute http(s) and resolves relative against the backend base', () => {
  assert.equal(resolveOpenTarget('https://example.test/docs', BASE), 'https://example.test/docs')
  assert.equal(resolveOpenTarget('http://example.test/docs?q=1#top', BASE), 'http://example.test/docs?q=1#top')
  assert.equal(resolveOpenTarget('/docs/a%20b?x=1#sec', BASE), `${BASE}/docs/a%20b?x=1#sec`)
  assert.equal(resolveOpenTarget('cards/one', BASE), `${BASE}/cards/one`)
  // No backend base means a relative reference cannot be resolved locally.
  assert.equal(resolveOpenTarget('/docs', null), null)
})

test('platform.openLink opens an absolute http(s) target and reports success', async () => {
  const { host, opened, posts } = build()
  await send(host, 'https://example.test/docs')
  assert.deepEqual(opened, ['https://example.test/docs'])
  assert.deepEqual(posts, [{ type: 'platform.openLink.result', requestId: 41, data: { opened: true } }])
})

test('platform.openLink resolves a relative target against the backend base with no token', async () => {
  const { host, opened } = build()
  await send(host, '/docs/a%20b?x=1#sec')
  assert.deepEqual(opened, [`${BASE}/docs/a%20b?x=1#sec`])
  assert.equal(String(opened[0]).includes('token'), false)
  assert.equal(String(opened[0]).includes('host-secret'), false)
})

test('platform.openLink rejects an invalid target with no side effect', async () => {
  const { host, opened } = build()
  await assert.rejects(send(host, 'javascript:alert(1)'), /Unsupported link target/)
  await assert.rejects(send(host, '//evil.test/x'), /Unsupported link target/)
  assert.deepEqual(opened, [], 'an unsupported reference never reaches openExternal')
})

test('platform.openLink rejects a stale ready epoch before any side effect', async () => {
  const { host, opened } = build()
  await assert.rejects(send(host, 'https://example.test/docs', { readyId: 999 }), /ownership changed/)
  assert.deepEqual(opened, [])
})

test('platform.openLink suppresses the result — not the open — when ready goes stale during the await', async () => {
  const opened = []
  let hostRef
  const { host, posts } = harness({
    backendBase: BASE,
    runtimeEpoch: READY,
    openExternal: async (url) => {
      opened.push(url)
      hostRef.beginReady(701)
      return true
    },
  })
  hostRef = host
  await send(host, 'https://example.test/docs')
  assert.deepEqual(opened, ['https://example.test/docs'], 'the browser was already opened')
  assert.deepEqual(posts, [], 'a stale document never receives the result')
})

test('platform.openLink reports a truthful failure when openExternal declines', async () => {
  const { host, opened, posts } = build({ openExternal: () => false })
  await assert.rejects(send(host, 'https://example.test/docs'), /Could not open link/)
  assert.deepEqual(opened, ['https://example.test/docs'])
  assert.deepEqual(posts, [], 'a declined open must not be reported as success')
})

test('platform.openLink refuses when no opener was injected', async () => {
  const { host } = harness({ backendBase: BASE, runtimeEpoch: READY })
  await assert.rejects(send(host, 'https://example.test/docs'), /Host cannot open external links/)
})
