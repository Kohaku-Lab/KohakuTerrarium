const assert = require('node:assert/strict')
const path = require('node:path')
const { createRequire } = require('node:module')
const test = require('node:test')

const frontendRequire = createRequire(path.resolve(__dirname, '../../../src/kohakuterrarium-frontend/package.json'))
const { JSDOM } = frontendRequire('jsdom')

test('notification surface supports levels, safe text, expiry, keyboard access, bounds and disposal', async () => {
  const { createNotificationSurface } = await import('../src/webview/notifications.mjs')
  const dom = new JSDOM('<!doctype html><body><button id="prior">Prior</button></body>')
  const document = dom.window.document
  const timers = new Set()
  const delays = []
  const surface = createNotificationSurface({
    document,
    maxVisible: 2,
    setTimer: (callback, delay) => {
      delays.push(delay)
      timers.add(callback)
      return callback
    },
    clearTimer: (callback) => timers.delete(callback),
  })
  try {
    const handle = surface.show({ type: 'warning', message: '<script>bad()</script>', duration: 0 })
    const warning = document.querySelector('[role="alert"]')
    assert.equal(warning.textContent.includes('<script>bad()</script>'), true)
    assert.equal(warning.querySelector('script'), null)
    assert.equal(warning.getAttribute('aria-live'), 'assertive')
    assert.equal(timers.size, 0)
    surface.show({ type: 'success', message: 'saved', duration: 5 })
    assert.equal(document.querySelector('[role="status"]').getAttribute('aria-live'), 'polite')
    assert.equal(timers.size, 1)
    surface.show('last')
    assert.equal(document.querySelectorAll('.kt-notification').length, 2)
    assert.equal(document.body.textContent.includes('bad()'), false)
    handle.close()
    assert.equal(document.querySelectorAll('.kt-notification').length, 2)
    for (const callback of [...timers]) callback()
    assert.equal(document.querySelectorAll('.kt-notification').length, 0)
    surface.show({ message: 'sticky', duration: 0 })
    document.querySelector('.kt-notification button').click()
    assert.equal(document.querySelectorAll('.kt-notification').length, 0)
    document.querySelector('#prior').focus()
    surface.show({ message: 'keyboard notification', duration: 5 })
    const interactive = document.querySelector('.kt-notification')
    const closeButton = interactive.querySelector('button')
    closeButton.focus()
    assert.equal(timers.size, 0, 'focus pauses expiry')
    interactive.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    assert.equal(document.activeElement.id, 'prior')
    surface.show({ message: 'hover notification', duration: 5 })
    const hovered = document.querySelector('.kt-notification')
    hovered.dispatchEvent(new dom.window.Event('mouseenter'))
    assert.equal(timers.size, 0)
    hovered.dispatchEvent(new dom.window.Event('mouseleave'))
    assert.equal(timers.size, 1)
    assert.equal(delays.at(-1), 5, 'leaving a notification grants a full reading interval')
    surface.clear()
    assert.equal(timers.size, 0)
    surface.show('dispose timer')
    surface.dispose()
    assert.equal(timers.size, 0)
    assert.equal(document.querySelector('.kt-notifications'), null)
    surface.show('late')
    assert.equal(document.querySelector('.kt-notifications'), null)
  } finally {
    surface.dispose()
    dom.window.close()
  }
})
