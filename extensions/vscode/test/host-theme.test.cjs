// Host theme boundary: the shared chat-ui ``dark:`` utilities key off a ``.dark``
// ancestor, so the webview mirrors VS Code's body theme (``vscode-dark`` /
// ``vscode-high-contrast`` / ``vscode-light`` / ``vscode-high-contrast-light``)
// onto ``<html>``. These pin the initial paint, the live switch, and the teardown.
const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const frontendRoot = path.resolve(root, '..', '..', 'src', 'kohakuterrarium-frontend')
const frontendRequire = createRequire(path.join(frontendRoot, 'package.json'))
const { JSDOM } = frontendRequire('jsdom')
const load = () => import(pathToFileURL(path.join(root, 'src', 'webview', 'hostTheme.mjs')).href)

function domWith(bodyClass = '') {
  const dom = new JSDOM('<!doctype html><html><body></body></html>')
  if (bodyClass) dom.window.document.body.className = bodyClass
  return dom
}
const settle = () => new Promise((resolve) => setImmediate(resolve))

test('isHostDark honors both VS Code high-contrast conventions', async () => {
  const { isHostDark } = await load()
  const body = (className) => domWith(className).window.document.body
  assert.equal(isHostDark(body('vscode-dark')), true)
  assert.equal(isHostDark(body('vscode-high-contrast')), true, 'high-contrast dark is dark')
  assert.equal(isHostDark(body('vscode-high-contrast-light')), false, 'high-contrast light is not dark')
  assert.equal(isHostDark(body('vscode-light')), false)
  assert.equal(isHostDark(body('')), false)
  assert.equal(isHostDark(null), false, 'a host without a body is treated as light')
})

test('installHostTheme mirrors the initial VS Code body theme onto <html>', async () => {
  const { installHostTheme } = await load()
  const cases = [
    ['vscode-dark', true],
    ['vscode-high-contrast', true],
    ['vscode-high-contrast-light', false],
    ['vscode-light', false],
  ]
  for (const [theme, dark] of cases) {
    const dom = domWith(theme)
    const dispose = installHostTheme({ document: dom.window.document })
    assert.equal(dom.window.document.documentElement.classList.contains('dark'), dark, theme)
    dispose()
  }
})

test('installHostTheme follows a live host theme switch', async () => {
  const { installHostTheme } = await load()
  const dom = domWith('vscode-light')
  const { document } = dom.window
  const dispose = installHostTheme({ document })
  assert.equal(document.documentElement.classList.contains('dark'), false)
  document.body.className = 'vscode-dark'
  await settle()
  assert.equal(document.documentElement.classList.contains('dark'), true, 'a switch to dark is picked up')
  document.body.className = 'vscode-high-contrast-light'
  await settle()
  assert.equal(document.documentElement.classList.contains('dark'), false, 'a switch to high-contrast light is picked up')
  dispose()
})

test('dispose stops observing so a disposed webview never writes again', async () => {
  const { installHostTheme } = await load()
  const dom = domWith('vscode-light')
  const { document } = dom.window
  const dispose = installHostTheme({ document })
  dispose()
  document.body.className = 'vscode-dark'
  await settle()
  assert.equal(document.documentElement.classList.contains('dark'), false, 'no write after dispose')
})

test('installHostTheme is inert without a document', async () => {
  const { installHostTheme } = await load()
  const dispose = installHostTheme({})
  assert.equal(typeof dispose, 'function')
  dispose()
})
