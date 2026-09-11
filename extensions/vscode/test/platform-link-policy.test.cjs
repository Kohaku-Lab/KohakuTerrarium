// Pure click-time policy of the webview platform link opener. These pin the
// capture rules the opener applies around one ``platform.openLink`` request:
// a click with no live ready epoch must never be sent (the Host refuses and
// drops an epoch-less envelope), and a failure that settles after the epoch
// moved on must be suppressed rather than reported as a failure of a link the
// browser may already have opened.
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

let policy
async function load() {
  policy ??= await import(pathToFileURL(path.resolve(__dirname, '..', 'src', 'webview', 'platformLinkPolicy.mjs')))
  return policy
}

test('isOpenReady admits only a live positive ready epoch', async () => {
  const { isOpenReady } = await load()
  assert.equal(isOpenReady(700), true)
  assert.equal(isOpenReady(1), true)
  for (const value of [null, undefined, 0, -1, 1.5, NaN, '700', '']) {
    assert.equal(isOpenReady(value), false, String(value))
  }
})

test('classifyFailure surfaces only a failure still owned by the captured epoch', async () => {
  const { classifyFailure } = await load()
  assert.equal(classifyFailure({ ownerReadyId: 700, currentReadyId: 700 }), 'failed')
  assert.equal(classifyFailure({ ownerReadyId: 700, currentReadyId: 701 }), 'stale')
  assert.equal(classifyFailure({ ownerReadyId: 700, currentReadyId: null }), 'stale')
})
