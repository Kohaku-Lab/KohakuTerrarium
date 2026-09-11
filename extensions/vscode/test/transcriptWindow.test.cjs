const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

async function helpers() {
  return import(pathToFileURL(path.join(root, 'src', 'webview', 'transcriptWindow.mjs')))
}

test('transcript bindings stay stable per identity and retain stale identity tokens', async () => {
  const { createTranscriptBindings } = await helpers()
  const calls = []
  const bindings = createTranscriptBindings({
    onViewportReady: (viewport, identity) => calls.push(['viewport', viewport, identity]),
    onScroll: (event, identity) => calls.push(['scroll', event, identity]),
    onReply: (payload) => calls.push(['reply', payload]),
  })

  const first = bindings.forIdentity('session-a:root')
  assert.equal(bindings.forIdentity('session-a:root'), first)
  const second = bindings.forIdentity('session-b:root')
  assert.notEqual(second, first)

  first.onViewportReady('old-viewport')
  first.onScroll('old-scroll')
  first.onReply({ actionId: 'accept' })
  assert.deepEqual(calls, [
    ['viewport', 'old-viewport', 'session-a:root'],
    ['scroll', 'old-scroll', 'session-a:root'],
    ['reply', { actionId: 'accept' }],
  ])
})
