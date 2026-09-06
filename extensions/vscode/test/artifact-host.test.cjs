const assert = require('node:assert/strict')
const test = require('node:test')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { ArtifactRegistry, canonicalArtifactPath } = require('../src/host/artifactRegistry.cjs')

const ref = (path) => `/api/sessions/${path}`

test('protocol accepts artifact.read with exact fields and rejects malformed envelopes', () => {
  assert.equal(
    allowedMessage({ type: 'artifact.read', requestId: 30, path: ref('graph_1/artifacts/img.png'), readyId: 7, selectionVersion: 0 }),
    true,
  )
  for (const message of [
    { type: 'artifact.read', requestId: 30 },
    { type: 'artifact.read', requestId: 30, path: '', readyId: 7, selectionVersion: 0 },
    { type: 'artifact.read', requestId: 30, path: ref('graph_1/artifacts/img.png'), readyId: 0, selectionVersion: 0 },
    { type: 'artifact.read', requestId: 30, path: ref('graph_1/artifacts/img.png'), readyId: 1.5, selectionVersion: 0 },
    { type: 'artifact.read', requestId: 30, path: ref('graph_1/artifacts/img.png'), readyId: 7, selectionVersion: -1 },
    {
      type: 'artifact.read',
      requestId: 30,
      path: ref('graph_1/artifacts/img.png'),
      readyId: 7,
      selectionVersion: 0,
      endpoint: 'http://127.0.0.1:8000',
    },
    { type: 'artifact.read', requestId: 30, path: ref('graph_1/artifacts/img.png'), readyId: 7, selectionVersion: 0, target: 'graph_1' },
    { type: 'artifact.read', requestId: 30, path: 42, readyId: 7, selectionVersion: 0 },
  ])
    assert.equal(allowedMessage(message), false, JSON.stringify(message))
})

test('canonical artifact paths accept backend-shaped refs with encoded segments', () => {
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/img.png')), ref('graph_1/artifacts/img.png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/sub/dir/p%20a.png')), ref('graph_1/artifacts/sub/dir/p%20a.png'))
  assert.equal(canonicalArtifactPath(ref('artifacts%2Dns/artifacts/x.webp')), ref('artifacts-ns/artifacts/x.webp'))
  assert.equal(canonicalArtifactPath('/api/sessions/graph_1/artifacts'), null)
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/')), null)
})

test('canonical validation rejects traversal that survives the backend double unquote', () => {
  for (const path of [
    ref('graph_1/artifacts/../secret.png'),
    ref('graph_1/artifacts/..%2fsecret.png'),
    ref('graph_1/artifacts/%2e%2e/secret.png'),
    ref('graph_1/artifacts/%252e%252e%252fsecret.png'),
    ref('graph_1/artifacts/a/..%5Csecret.png'),
    ref('graph_1/artifacts/..'),
    ref('graph_1/artifacts/.'),
    ref('graph_1/%2e%2e/artifacts/x.png'),
    ref('graph_1/artifacts/%2e%2e%2f%2e%2e%2fsecret'),
  ])
    assert.equal(canonicalArtifactPath(path), null, path)
})

test('canonical validation rejects residual percents that imply further decode levels', () => {
  for (const path of [
    // Triple-encoded: decodes to '%2e%2e%2fsecret.png' with every literal separator hidden.
    ref('graph_1/artifacts/%25252e%25252e%25252fsecret.png'),
    ref('graph_1/artifacts/%25252fsecret.png'),
    ref('graph_1/artifacts/a%25252fb.png'),
    ref('graph_1/artifacts/%25253f%25252fx.png'),
    // Four levels of encoding still leaves a residual percent after the backend double unquote.
    ref('graph_1/artifacts/%2525252e.png'),
    // Triple-encoded namespace could decode to '..' on a later backend pass.
    ref('%25252e%25252e/artifacts/x.png'),
    ref('%25252e%25252e%25252fgraph_1/artifacts/x.png'),
  ])
    assert.equal(canonicalArtifactPath(path), null, path)
  // Well-formed single-encoded names stay admissible.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/p%20a.png')), ref('graph_1/artifacts/p%20a.png'))
})

test('extraction captures the exact URL across balanced parens and brace characters', () => {
  const registry = new ArtifactRegistry()
  registry.observe({
    text: `![a](${ref('graph_1/artifacts/a(b).png')}) nested (${ref('graph_1/artifacts/p(1).png')}) braces ${ref('graph_1/artifacts/x{1}.png')} end`,
  })
  assert.equal(registry.allowed(ref('graph_1/artifacts/a(b).png')), true)
  assert.equal(registry.allowed(ref('graph_1/artifacts/p(1).png')), true)
  assert.equal(registry.allowed(ref('graph_1/artifacts/x{1}.png')), true)
  // The canonical form re-encodes braces exactly once and leaves parens intact.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/x{1}(a).png')), ref('graph_1/artifacts/x%7B1%7D(a).png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/x%7B1%7D(a).png')), ref('graph_1/artifacts/x%7B1%7D(a).png'))
})

test('canonical validation rejects separators, encoded traversal, and non-path forms', () => {
  for (const path of [
    'http://127.0.0.1:8000/api/sessions/graph_1/artifacts/x.png',
    '//api/sessions/graph_1/artifacts/x.png',
    '/api/sessions/graph_1/artifacts/x.png?token=1',
    '/api/sessions/graph_1/artifacts/x.png#frag',
    '/api/sessions/graph_1/artifacts/x%5C.png',
    ref('graph_1\\artifacts\\x.png'),
    '/api/sessions/graph_1/artifacts/x%00.png',
    '/api/sessions/graph_1/artifacts/%zz.png',
    '/api/sessions//artifacts/x.png',
    '/api/sessions/graph_1/other/x.png',
    '/api/sessions/graph_1/artifacts/x.png/y/..',
    '/other/path',
    42,
    null,
  ])
    assert.equal(canonicalArtifactPath(path), null, String(path))
})

test('registry admits image_url and markdown refs observed in bounded history scans', () => {
  const registry = new ArtifactRegistry()
  const admitted = registry.observe({
    events: [
      { message: { contentParts: [{ type: 'image_url', image_url: { url: ref('graph_1/artifacts/img.png') } }] } },
      { tool: { data: { markdown: `see ![a](${ref('graph_1/artifacts/p%20a.webp')}) and https://cdn.example/evil.png` } } },
      { note: `plain ${ref('other_ns/artifacts/deep/b.gif')} tail` },
    ],
  })
  assert.ok(admitted >= 3)
  assert.equal(registry.allowed(ref('graph_1/artifacts/img.png')), true)
  assert.equal(registry.allowed(ref('graph_1/artifacts/p%20a.webp')), true)
  assert.equal(registry.allowed(ref('other_ns/artifacts/deep/b.gif')), true)
  assert.equal(registry.allowed(ref('graph_1/artifacts/unknown.png')), false)
  assert.equal(registry.allowed('https://cdn.example/evil.png'), false)
})

test('registry scan is bounded by refs, bytes, and depth', () => {
  const tiny = new ArtifactRegistry({ maxRefs: 3, maxScanBytes: 2000, maxNodes: 100, maxDepth: 4, maxPathLength: 2048 })
  const many = Array.from({ length: 50 }, (_, index) => ({ url: ref(`graph_1/artifacts/${index}.png`) }))
  assert.equal(tiny.observe(many), 3)
  const flooded = { text: ref('graph_1/artifacts/a.png').repeat(3000) }
  const sparse = new ArtifactRegistry({ maxRefs: 10, maxScanBytes: 100, maxNodes: 100, maxDepth: 4, maxPathLength: 2048 })
  assert.equal(sparse.observe(flooded), 0)
  const deep = { payload: null }
  let cursor = deep
  for (let index = 0; index < 64; index++) cursor = cursor.child = { payload: null }
  cursor.payload = { url: ref('graph_1/artifacts/deep.png') }
  const shallow = new ArtifactRegistry()
  shallow.observe(deep)
  assert.equal(shallow.allowed(ref('graph_1/artifacts/deep.png')), false)
})

test('registry collects from realtime frames and invalidates on demand', () => {
  const registry = new ArtifactRegistry()
  registry.observeFrameText(JSON.stringify({ type: 'chunk', parts: [{ image_url: { url: ref('graph_1/artifacts/live.png') } }] }))
  assert.equal(registry.allowed(ref('graph_1/artifacts/live.png')), true)
  registry.observeFrameText('not json')
  registry.observeFrameText(JSON.stringify({ huge: 'x'.repeat(3_000_000) }))
  registry.observeFrameText(JSON.stringify({ nested: JSON.stringify({ url: ref('graph_1/artifacts/inner.png') }) }))
  assert.equal(registry.allowed(ref('graph_1/artifacts/inner.png')), true)
  registry.invalidate()
  assert.equal(registry.allowed(ref('graph_1/artifacts/live.png')), false)
  assert.equal(registry.allowed(ref('graph_1/artifacts/inner.png')), false)
})

test('scanned filenames keep encoded spaces and balanced parentheses', () => {
  const registry = new ArtifactRegistry()
  registry.observe({
    note: `see /api/sessions/graph_1/artifacts/img(1).png and ![a](${ref('graph_1/artifacts/p%20x.webp')}) end`,
  })
  assert.equal(registry.allowed(ref('graph_1/artifacts/img(1).png')), true)
  assert.equal(registry.allowed(ref('graph_1/artifacts/p%20x.webp')), true)
})

test('absolute URLs embedding the artifact route never authorize the extracted path', () => {
  const registry = new ArtifactRegistry()
  registry.observe({
    text: `see https://evil.example${ref('graph_1/artifacts/leak.png')} and http://127.0.0.1:8000${ref('graph_1/artifacts/host.png')}`,
  })
  assert.equal(registry.allowed(ref('graph_1/artifacts/leak.png')), false)
  assert.equal(registry.allowed(ref('graph_1/artifacts/host.png')), false)
})
