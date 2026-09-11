// Host locale boundary: the webview owns no dictionary and no persistence. It
// reads the language the extension resolved into ``<html lang>`` (from
// ``vscode.env.language``) and refuses to pretend a locale write it cannot send
// back to the host.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const load = (name) => import(pathToFileURL(path.join(root, 'src', 'webview', name)).href)

test('hostLocale trims <html lang> and falls back to English', async () => {
  const { hostLocale } = await load('hostLocale.mjs')
  const doc = (lang) => ({ documentElement: { getAttribute: () => lang } })

  assert.equal(hostLocale(doc('zh-cn')), 'zh-cn')
  assert.equal(hostLocale(doc('  ja  ')), 'ja')
  assert.equal(hostLocale(doc('')), 'en')
  assert.equal(hostLocale(doc(null)), 'en')
  assert.equal(hostLocale({}), 'en')
  assert.equal(hostLocale(), 'en', 'no global document in node falls back to English')
})

test('the host pref seam exposes reads only — no locale write it cannot persist', async () => {
  const seam = await load('hostLocale.mjs')
  const previous = globalThis.document
  globalThis.document = { documentElement: { getAttribute: () => 'ja' } }
  try {
    assert.equal(seam.getHostPref(seam.LOCALE_PREF_KEY, 'en'), 'ja', 'the host language wins over the fallback')
    assert.equal(seam.getHostPref('theme', 'system'), 'system', 'non-locale prefs fall back: the extension persists nothing')
    assert.equal(seam.getHostPref('kt-locale'), 'ja')
  } finally {
    globalThis.document = previous
  }
  assert.deepEqual(
    Object.keys(seam).sort(),
    ['LOCALE_PREF_KEY', 'getHostPref', 'hostLocale', 'rejectHostPrefWrite'],
    'no writable setter is exposed',
  )
})

test('the host pref seam rejects writes it cannot round-trip', async () => {
  const { rejectHostPrefWrite, LOCALE_PREF_KEY } = await load('hostLocale.mjs')
  assert.throws(() => rejectHostPrefWrite(LOCALE_PREF_KEY), /read-only for host preferences/)
  assert.throws(() => rejectHostPrefWrite(LOCALE_PREF_KEY), new RegExp(LOCALE_PREF_KEY), 'the key is named in the failure')
  assert.throws(() => rejectHostPrefWrite('theme'), /"theme"/)
})

test('webview pref shim reads the host seam and rejects locale writes it cannot persist', () => {
  const source = fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'misc.js'), 'utf8')
  assert.match(source, /import \{ getHostPref, rejectHostPrefWrite \} from '\.\.\/hostLocale\.mjs'/)
  assert.match(source, /export const getHybridPrefSync = \(key, fallback = null\) => getHostPref\(key, fallback\)/)
  assert.match(
    source,
    /export const setHybridPref = \(key\) => rejectHostPrefWrite\(key\)/,
    'a locale write must reject instead of silently succeeding',
  )
})
