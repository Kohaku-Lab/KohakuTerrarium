'use strict'

const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const moduleUrl = pathToFileURL(path.resolve(__dirname, '../src/webview/contextCommandResult.mjs'))

test('context command result parser surfaces successful HTTP errors', async () => {
  const { parseContextCommandResult } = await import(moduleUrl)
  assert.deepEqual(parseContextCommandResult({ error: 'Compaction failed' }), {
    error: 'Compaction failed',
    status: '',
  })
})

test('context command result parser surfaces backend messages and normal output as status', async () => {
  const { parseContextCommandResult } = await import(moduleUrl)
  assert.deepEqual(parseContextCommandResult({ data: { message: 'Context compacted' } }), {
    error: '',
    status: 'Context compacted',
  })
  assert.deepEqual(parseContextCommandResult({ data: { type: 'notify', message: 'Done', level: 'success' } }), {
    error: '',
    status: 'Done',
  })
  assert.deepEqual(parseContextCommandResult({ notify: { message: 'Cleared' } }), {
    error: '',
    status: 'Cleared',
  })
  assert.deepEqual(parseContextCommandResult({ output: 'Compact complete' }), {
    error: '',
    status: 'Compact complete',
  })
})

test('empty, ok, and cancelled context results remain silent', async () => {
  const { parseContextCommandResult } = await import(moduleUrl)
  for (const response of [undefined, {}, { ok: true }, { cancelled: true }, { cancelled: true, superseded: true }]) {
    assert.deepEqual(parseContextCommandResult(response), { error: '', status: '' })
  }
})

test('context result application ignores stale results and errors', async () => {
  const { applyContextCommandOutcome } = await import(moduleUrl)
  const shown = []
  assert.equal(
    applyContextCommandOutcome({ error: 'old' }, false, (kind, text) => shown.push([kind, text])),
    false,
  )
  assert.equal(
    applyContextCommandOutcome({ output: 'old status' }, false, (kind, text) => shown.push([kind, text])),
    false,
  )
  assert.deepEqual(shown, [])

  assert.equal(
    applyContextCommandOutcome({ output: 'current' }, true, (kind, text) => shown.push([kind, text])),
    true,
  )
  assert.deepEqual(shown, [['status', 'current']])
})
