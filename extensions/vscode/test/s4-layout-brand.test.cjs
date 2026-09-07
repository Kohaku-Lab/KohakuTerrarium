const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const repository = path.resolve(root, '..', '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')

test('webview uses flat session, chat, and composer regions with an accessible collapsed disclosure', () => {
  const source = read('src/webview/index.js')
  assert.match(source, /const sessionsExpanded = ref\(false\)/)
  assert.match(source, /class: 'session-region'/)
  assert.match(source, /class: 'chat-region'/)
  assert.match(source, /class: 'composer-region'/)
  assert.match(source, /aria-expanded/)
  assert.match(source, /'aria-controls': 'session-list'/)
  assert.match(source, /id: 'session-list'/)
  assert.match(source, /h\('section', \{ class: 'chat-region' \}, \[\s*h\(ChatTranscriptSection/s)
  const renderers = read('src/webview/viewRenderers.mjs')
  assert.match(renderers, /session\.creatures\.length === 1/)
  for (const label of ['New Session', 'Refresh Sessions', 'Stop Session']) assert.match(source, new RegExp(label))
})

test('flat layout reserves transcript scrolling for chat and keeps only composer shell rounded', () => {
  const css = read('src/webview/style.css')
  assert.match(css, /\.kt-conversation-host\s*\{[^}]*display:\s*flex[^}]*min-height:\s*0/s)
  assert.match(css, /\.chat-region\s*\{[^}]*flex:\s*1[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s)
  assert.match(css, /\.composer-region\s*\{[^}]*border-top:/s)
  assert.match(css, /\.session-list\s*\{[^}]*overflow-y:\s*auto/s)
  assert.match(css, /\.kt-conversation-host \.kt-chat-composer__shell\s*\{[^}]*border-color:/s)
  assert.doesNotMatch(css, /\.(?:session-region|session-row)\s*\{[^}]*(?:border-radius|box-shadow):/s)
  assert.match(css, /@media \(max-width:/)
  assert.match(css, /vscode-contrastBorder/)
})

test('sidebar conversation uses host-scoped gutters without horizontal overflow', () => {
  const css = read('src/webview/style.css')
  assert.match(css, /\.chat-region\s*\{[^}]*padding:\s*8px 0(?:px)?\s*;/s)
  assert.match(
    css,
    /\.kt-conversation-host \.kt-transcript-viewport\s*\{[^}]*overflow-x:\s*hidden\s*;[^}]*padding-inline:\s*(?:\.5|0\.5)rem\s*;/s,
  )
  assert.match(css, /\.kt-conversation-host \.kt-conversation-message--assistant\s*\{[^}]*width:\s*100%\s*;[^}]*max-width:\s*none\s*;/s)
  assert.match(
    css,
    /@media \(max-width:\s*420px\)[^{]*\{[^}]*\.kt-conversation-host \.kt-conversation-message--user\s*\{[^}]*width:\s*100%\s*;/s,
  )
  assert.doesNotMatch(css, /(?:^|})\s*\.(?:kt-transcript-viewport|kt-conversation-message--(?:assistant|user))\s*\{/m)
})

test('official brand is packaged and wired to manifest, Activity Bar, and webview resource URI', () => {
  const manifest = JSON.parse(read('package.json'))
  assert.equal(manifest.icon, 'media/kohaku-icon.png')
  assert.equal(manifest.contributes.viewsContainers.activitybar[0].icon, 'media/kohaku-icon.png')
  assert.equal(fs.existsSync(path.join(root, 'media', 'kohakuterrarium.svg')), false)
  const canonical = fs.readFileSync(path.join(repository, 'src', 'kohakuterrarium-frontend', 'public', 'kohaku-icon.png'))
  const packaged = fs.readFileSync(path.join(root, 'media', 'kohaku-icon.png'))
  assert.equal(crypto.createHash('sha256').update(packaged).digest('hex'), crypto.createHash('sha256').update(canonical).digest('hex'))

  const host = read('src/extension.cjs')
  const html = read('src/host/webview.cjs')
  const webview = read('src/webview/index.js')
  assert.match(host, /localResourceRoots:[^\]]*'dist'[^\]]*'media'/s)
  assert.match(host, /brandUri:/)
  assert.match(html, /data-brand-uri="\$\{brandUri\}"/)
  assert.match(webview, /class: 'brand-mark'/)
  assert.match(webview, /brandUri/)
})
