// Owner-fenced metadata read-back: a switch captures a stable owner AND the exact
// per-creature entry it wrote, and a read-back applies only to that one owned
// creature. These pin the fence and the "never clobber a newer per-creature
// state" rule without booting the whole webview.
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const moduleUrl = pathToFileURL(path.resolve(__dirname, '..', 'src', 'webview', 'modelMetadata.mjs'))

test('a read-back is owned only while the exact session, creature and host epoch still hold', async () => {
  const { createMetadataReadback } = await import(moduleUrl)
  let current = { session: { runtimeId: 'g1' }, targetCreatureId: 'c1' }
  let epoch = 0
  const readback = createMetadataReadback({
    getOwner: () => ({ runtimeId: current?.session?.runtimeId, creatureId: current?.targetCreatureId, epoch }),
    getEntry: () => undefined,
  })
  const owner = readback.captureOwner()
  assert.equal(readback.ownsOwner(owner), true)

  // A target change at the same ready supersedes the owner.
  current = { session: { runtimeId: 'g1' }, targetCreatureId: 'c2' }
  assert.equal(readback.ownsOwner(owner), false)

  // A session restart supersedes it.
  current = { session: { runtimeId: 'g2' }, targetCreatureId: 'c2' }
  assert.equal(readback.ownsOwner(owner), false)

  // A host ready-epoch change supersedes it even for the same session/creature.
  current = { session: { runtimeId: 'g1' }, targetCreatureId: 'c1' }
  epoch = 1
  assert.equal(readback.ownsOwner(owner), false)
  assert.equal(readback.owns({ owner, entryKey: 'root', entry: undefined }), false)
})

test('a read-back whose entry was replaced by a newer write is not applied', async () => {
  const { createMetadataReadback } = await import(moduleUrl)
  const entries = { root: { llmName: 'codex/a' } }
  const readback = createMetadataReadback({
    getOwner: () => ({ runtimeId: 'g1', creatureId: 'c1', epoch: 0 }),
    getEntry: (key) => entries[key],
  })
  const owner = readback.captureOwner()
  const captured = readback.captureEntry('root', owner)
  assert.equal(readback.owns(captured), true)

  // A newer switch (or a newer session_info) replaces the entry object.
  entries.root = { llmName: 'codex/b' }
  assert.equal(readback.owns(captured), false, 'a superseded entry must not be overwritten')

  // A newer write to an unrelated creature leaves the owned entry intact.
  entries.beta = { llmName: 'codex/c' }
  entries.root = captured.entry
  assert.equal(readback.owns(captured), true)
})

test('a read-back applies only to its owned creature, keeping canonical and max_context', async () => {
  const { selectOwnedMetadata } = await import(moduleUrl)
  const metadata = {
    creatures: [
      { name: 'root', llm_name: 'codex/other@effort=high', model: 'other', max_context: 200000 },
      { name: 'beta', llm_name: 'codex/kept', model: 'kept', max_context: 999999 },
    ],
  }
  const entry = selectOwnedMetadata(metadata, 'root', 'codex/other@effort=high', { llmName: 'codex/other@effort=high' })
  assert.deepEqual(entry, { model: 'other', llmName: 'codex/other@effort=high', maxContext: 200000 })

  // The non-target creature is never returned, so its newer session_info (same
  // canonical, newer max_context) can never be clobbered by this read-back.
  assert.equal(selectOwnedMetadata(metadata, 'beta', 'codex/other@effort=high', { maxContext: 999999 }), null)
  assert.equal(selectOwnedMetadata(metadata, 'gamma', 'codex/kept', {}), null)

  // A stale read-back that no longer reports the accepted canonical is dropped.
  assert.equal(selectOwnedMetadata(metadata, 'root', 'codex/moved', {}), null)
})
