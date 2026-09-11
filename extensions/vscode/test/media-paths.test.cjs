// The canonical artifact route the Host will fetch for a media reference. The
// route helper is the only gate between a webview-supplied reference and the
// network, so it must accept every name the backend actually serves (a literal
// ``%`` the backend emits as ``%25``, ``#``, ``?``, unicode) while refusing the
// traversal shapes. The accepted/rejected split is pinned against the real
// backend by ``tests/unit/api/test_media_paths_parity.py`` (a live uvicorn
// server), not guessed here.
const assert = require('node:assert/strict')
const test = require('node:test')

const { canonicalArtifactPath, mediaFetchTarget } = require('../src/host/mediaPaths.cjs')

const ref = (path) => `/api/sessions/${path}`

test('canonical artifact paths preserve the exact valid percent-encoding of a backend route', () => {
  // A route the backend emitted is returned byte-for-byte, never re-encoded: the
  // backend re-decodes what it emitted, so re-encoding a literal percent would
  // corrupt the target (``%2528`` must stay ``%2528``, not become ``(``).
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/img.png')), ref('graph_1/artifacts/img.png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/sub/dir/p%20a.png')), ref('graph_1/artifacts/sub/dir/p%20a.png'))
  assert.equal(canonicalArtifactPath(ref('artifacts%2Dns/artifacts/x.webp')), ref('artifacts%2Dns/artifacts/x.webp'))
  // The reserved ``#?%`` a backend ``quote(..., safe='/')`` produces stay intact.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/a%23b.png')), ref('graph_1/artifacts/a%23b.png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/a%25b.png')), ref('graph_1/artifacts/a%25b.png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/%C3%A9.png')), ref('graph_1/artifacts/%C3%A9.png'))
  // A bare ``{``/``}``/paren is a filename character; it is preserved as sent.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/x{1}(a).png')), ref('graph_1/artifacts/x{1}(a).png'))
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/x%7B1%7D(a).png')), ref('graph_1/artifacts/x%7B1%7D(a).png'))
  assert.equal(canonicalArtifactPath('/api/sessions/graph_1/artifacts'), null)
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/')), null)
  // A canonical artifact route is exactly what ``mediaFetchTarget`` resolves.
  assert.equal(mediaFetchTarget(ref('graph_1/artifacts/img.png')), ref('graph_1/artifacts/img.png'))
})

test('a literal percent (even a nested percent-looking name) stays a valid backend name', () => {
  // ``a%25b.png`` -> the backend resolves the file ``a%b.png``; the route is kept.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/a%25b.png')), ref('graph_1/artifacts/a%25b.png'))
  // ``a%2528b.png`` -> the file ``a%28b.png`` (a literal percent-two-eight name).
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/a%2528b.png')), ref('graph_1/artifacts/a%2528b.png'))
  // ``a%252520b.png`` -> the file ``a%20b.png`` (a literal percent-looking name).
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/a%252520b.png')), ref('graph_1/artifacts/a%252520b.png'))
  // An invalid ``%`` escape is left verbatim by the backend, so it is a name too.
  assert.equal(canonicalArtifactPath(ref('graph_1/artifacts/%zz.png')), ref('graph_1/artifacts/%zz.png'))
  // ``%25252e`` decodes (twice) to a literal ``%2e`` filename, never a dot.
  assert.equal(
    canonicalArtifactPath(ref('graph_1/artifacts/%25252e%25252e%25252fsecret.png')),
    ref('graph_1/artifacts/%25252e%25252e%25252fsecret.png'),
  )
})

test('canonical validation rejects traversal that survives the backend double unquote', () => {
  for (const path of [
    ref('graph_1/artifacts/../secret.png'),
    ref('graph_1/artifacts/..%2fsecret.png'),
    ref('graph_1/artifacts/%2e%2e/secret.png'),
    ref('graph_1/artifacts/%252e%252e%252fsecret.png'),
    ref('graph_1/artifacts/%252e%252e%252f%252e%252e%252fsecret.png'),
    ref('graph_1/artifacts/a/..%5Csecret.png'),
    ref('graph_1/artifacts/..'),
    ref('graph_1/artifacts/.'),
    ref('graph_1/%2e%2e/artifacts/x.png'),
    ref('graph_1/artifacts/%2e%2e%2f%2e%2e%2fsecret'),
  ])
    assert.equal(canonicalArtifactPath(path), null, path)
})

test('canonical validation rejects separators, controls, and non-path forms', () => {
  for (const path of [
    'http://127.0.0.1:8000/api/sessions/graph_1/artifacts/x.png',
    '//api/sessions/graph_1/artifacts/x.png',
    '/api/sessions/graph_1/artifacts/x.png?token=1',
    '/api/sessions/graph_1/artifacts/x.png#frag',
    '/api/sessions/graph_1/artifacts/x%5C.png',
    ref('graph_1\\artifacts\\x.png'),
    '/api/sessions/graph_1/artifacts/x%00.png',
    '/api/sessions//artifacts/x.png',
    '/api/sessions/graph_1/other/x.png',
    '/api/sessions/graph_1/artifacts/x.png/y/..',
    '/other/path',
    42,
    null,
  ])
    assert.equal(canonicalArtifactPath(path), null, String(path))
})

test('a refused route is never reinterpreted as a local raw file path', () => {
  for (const value of ['/api/sessions/graph_1/artifacts/../secret.png', '/api/sessions/../artifacts/x.png'])
    assert.equal(mediaFetchTarget(value), null, value)
})
