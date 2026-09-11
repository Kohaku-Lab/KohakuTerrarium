// Regression: the scroll tick's scheduler and canceller must be paired at
// schedule time. When requestAnimationFrame is unavailable the composable
// falls back to setTimeout, and dispose() must cancel that fallback timer
// with clearTimeout instead of leaking it through cancelAnimationFrame.
const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.resolve(root, '../../src/kohakuterrarium-frontend/package.json'))
const { JSDOM } = frontendRequire('jsdom')
const importLocal = (name) => import(pathToFileURL(require.resolve(name)))

let harnessCode

async function buildHarness() {
  if (harnessCode) return harnessCode
  const { build } = await importLocal('vite')
  const { default: config } = await import(pathToFileURL(path.join(root, 'vite.config.mjs')))
  const { default: vue } = await importLocal('@vitejs/plugin-vue')
  const { default: autoImport } = await importLocal('unplugin-auto-import/vite')
  const result = await build({
    ...config,
    configFile: false,
    logLevel: 'silent',
    plugins: [
      vue(),
      ...config.plugins.filter((plugin) => plugin?.name !== 'vite:vue' && plugin?.name !== 'unplugin-auto-import'),
      autoImport({ imports: ['vue', 'pinia'], dts: false }),
    ],
    build: {
      ...config.build,
      write: false,
      sourcemap: false,
      minify: false,
      lib: {
        entry: path.join(__dirname, 'fixtures', 'transcriptPagingHarness.js'),
        formats: ['iife'],
        name: 'TranscriptPagingHarness',
        fileName: () => 'transcriptPagingHarness.js',
      },
    },
  })
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((item) => item.output)
  harnessCode = outputs.find((item) => item.type === 'chunk' && item.isEntry).code
  return harnessCode
}

async function boot() {
  const dom = new JSDOM('<!doctype html>', {
    url: 'https://webview.test/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  })
  dom.window.eval(await buildHarness())
  return dom
}

const viewport = { scrollTop: 0, scrollHeight: 1000, clientHeight: 200 }

test('dispose cancels the setTimeout fallback scroll tick when rAF is unavailable', async () => {
  const dom = await boot()
  const { window } = dom
  try {
    window.requestAnimationFrame = undefined
    window.cancelAnimationFrame = undefined
    const scheduled = []
    const cleared = []
    window.setTimeout = (callback, delay) => {
      scheduled.push({ callback, delay })
      return 4242
    }
    window.clearTimeout = (id) => cleared.push(id)

    const paging = window.TranscriptPagingHarness.createPaging(viewport)
    paging.onScroll()
    assert.equal(scheduled.length, 1, 'the fallback path schedules exactly one timer')
    assert.deepEqual(cleared, [], 'nothing is cancelled before dispose')

    paging.dispose()
    assert.deepEqual(cleared, [4242], 'dispose cancels the fallback timer with clearTimeout')
    assert.equal(scheduled.length, 1, 'dispose schedules nothing new')
  } finally {
    window.close()
  }
})

test('dispose cancels the requestAnimationFrame scroll tick when rAF is available', async () => {
  const dom = await boot()
  const { window } = dom
  try {
    const frames = []
    const canceled = []
    window.requestAnimationFrame = (callback) => {
      frames.push(callback)
      return 777
    }
    window.cancelAnimationFrame = (id) => canceled.push(id)

    const paging = window.TranscriptPagingHarness.createPaging(viewport)
    paging.onScroll()
    assert.equal(frames.length, 1, 'the animation-frame path schedules exactly one frame')
    assert.deepEqual(canceled, [], 'nothing is cancelled before dispose')

    paging.dispose()
    assert.deepEqual(canceled, [777], 'dispose cancels the frame with cancelAnimationFrame')
  } finally {
    window.close()
  }
})
