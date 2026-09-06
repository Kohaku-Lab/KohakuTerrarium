const assert = require('node:assert/strict')
const test = require('node:test')

const { publicError } = require('../src/host/errors.cjs')

test('Host errors are mapped to stable public messages without filesystem details', () => {
  assert.deepEqual(publicError('session.create', Error('Config C:/secret/config.yaml not found')), {
    code: 'session_create_failed',
    message: 'Could not create the Session. Check the Creature setting and workspace.',
  })
  assert.deepEqual(publicError('session.select', Error('C:/workspace vanished')), {
    code: 'session_select_failed',
    message: 'Could not select that Creature. Refresh Sessions and try again.',
  })
  assert.match(publicError('goal.execute').message, /may have executed/i)
  assert.match(publicError('goal.execute').message, /before retry/i)
  assert.deepEqual(publicError('ws.send', Error('token=secret')), {
    code: 'chat_transport_failed',
    message: 'The chat connection failed. Reconnect to the selected Creature.',
  })
})
