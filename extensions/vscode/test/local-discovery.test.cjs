const assert = require('node:assert/strict')
const test = require('node:test')

const { probeCapabilities, verifyKtProbe } = require('../src/host/localDiscovery.cjs')

function hungBodyResponse(signal) {
  return {
    ok: true,
    status: 200,
    json() {
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason || Error('aborted')), { once: true }))
    },
  }
}

for (const [name, probe] of [
  ['capabilities', probeCapabilities],
  ['diagnostics', verifyKtProbe],
]) {
  test(`${name} probe aborts streaming error bodies`, async () => {
    const originalFetch = global.fetch
    let signal
    global.fetch = async (_url, options) => {
      signal = options.signal
      return { ok: false, status: 401 }
    }
    try {
      await assert.rejects(probe('http://127.0.0.1:8001', 5))
      assert.equal(signal.aborted, true)
    } finally {
      global.fetch = originalFetch
    }
  })

  test(`${name} probe timeout covers response body reads`, async () => {
    const originalFetch = global.fetch
    let signal
    global.fetch = async (_url, options) => {
      signal = options.signal
      return hungBodyResponse(signal)
    }
    try {
      await assert.rejects(probe('http://127.0.0.1:8001', 5))
      assert.equal(signal.aborted, true)
    } finally {
      global.fetch = originalFetch
    }
  })
}
