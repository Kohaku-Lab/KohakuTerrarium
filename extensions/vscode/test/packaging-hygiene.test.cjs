// Packaging hygiene: the built VSIX must exclude temp/log artifacts while keeping the
// runtime entry bundle, webview assets, LICENSE and README. This asserts against the
// REAL @vscode/vsce file-selection pipeline (listFiles) rather than a regex over source,
// by pointing vsce at a synthetic extension mirror that copies this extension's
// .vscodeignore. A pre-fix baseline ignore file proves the temp/log fixtures are
// otherwise packageable (RED); the real ignore file excludes them (GREEN).
const assert = require('node:assert/strict')
const os = require('node:os')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const { listFiles, PackageManager } = require('@vscode/vsce')

const EXT_ROOT = path.resolve(__dirname, '..')
const IGNORE_FILE = path.join(EXT_ROOT, '.vscodeignore')
const MANIFEST = JSON.parse(fs.readFileSync(path.join(EXT_ROOT, 'package.json'), 'utf8'))

// Snapshot of the pre-fix .vscodeignore (no temp/log rules), used only to prove the
// temp/log fixtures are genuinely selectable when the hygiene rules are absent.
const PRE_FIX_BASELINE = [
  'src/**',
  'test/**',
  'scripts/**',
  'node_modules/**',
  '*.vsix',
  'package-lock.json',
  'vite.config.mjs',
  'dist/*.map',
].join('\n')

const TAG = 'packaging-hygiene-fixture'

// Temp/log artifacts that must never ship (root-level and nested).
const TEMP_FIXTURES = [`${TAG}.log`, `.tmp-${TAG}.log`, `${TAG}-scratch.tmp`, `scratch/${TAG}.log`, `scratch/${TAG}.tmp`]

function entryPath() {
  const main = typeof MANIFEST.main === 'string' ? MANIFEST.main : './dist/extension.cjs'
  return main.replace(/^\.\//, '')
}

// Runtime assets that must always ship.
function requiredAssets() {
  return [entryPath(), 'dist/webview.js', 'dist/webview.css', 'LICENSE', 'README.md', 'media/kohaku-icon.png'].sort()
}

async function selectWith(ignoreContent) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kt-packaging-'))
  try {
    const manifest = { name: MANIFEST.name, version: MANIFEST.version, publisher: MANIFEST.publisher, engines: MANIFEST.engines }
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify(manifest))
    fs.writeFileSync(path.join(dir, '.vscodeignore'), ignoreContent)
    for (const rel of [...requiredAssets(), ...TEMP_FIXTURES]) {
      const target = path.join(dir, rel)
      fs.mkdirSync(path.dirname(target), { recursive: true })
      fs.writeFileSync(target, 'fixture')
    }
    return await listFiles({ cwd: dir, packageManager: PackageManager.None })
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

test('pre-fix baseline: temp/log fixtures are selectable by vsce (RED)', async () => {
  const files = await selectWith(PRE_FIX_BASELINE)
  for (const fixture of TEMP_FIXTURES) assert.ok(files.includes(fixture), `baseline should include ${fixture}`)
})

test('real .vscodeignore excludes temp/log artifacts and keeps runtime assets (GREEN)', async () => {
  const ignore = fs.readFileSync(IGNORE_FILE, 'utf8')
  const files = await selectWith(ignore)
  for (const fixture of TEMP_FIXTURES) assert.ok(!files.includes(fixture), `should exclude ${fixture}`)
  for (const asset of requiredAssets()) assert.ok(files.includes(asset), `should retain ${asset}`)
  assert.ok(!files.some((f) => /\.(log|tmp)$/i.test(f) || /(^|\/)\.tmp/.test(f)), 'no temp/log artifacts selected')
})
