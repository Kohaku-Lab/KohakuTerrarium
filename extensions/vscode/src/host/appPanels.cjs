// ``kt.app`` apps in their own editor tabs. A chat webview cannot run app
// frames (its nonce CSP reaches every nested frame), so each app gets a
// WebviewPanel whose page is the app document behind a fixed outer CSP: no
// network connections, no frames, no forms, no local resources. Every script
// in that page is model-written, so the panel is untrusted: only JSON-RPC
// bridge messages are relayed to the chat webview, and a ``kt.send`` reaches
// the agent only after the user says yes to it here.
const { canonicalArtifactPath } = require('./mediaPaths.cjs')

const APP_TYPES = new Set(['app.document', 'app.open', 'app.post', 'app.close'])
const APP_ID = /^[A-Za-z0-9_.:-]{1,128}$/
const MAX_TITLE_CHARS = 200
const MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
const MAX_MESSAGE_BYTES = 1024 * 1024
const METHOD = /^[A-Za-z0-9_./:-]{1,64}$/

// The outer policy intersects with the app's own CSP meta (first in its document).
const PANEL_POLICY = [
  "default-src 'none'",
  "script-src 'unsafe-inline' https:",
  "style-src 'unsafe-inline' https:",
  'img-src data: blob: https:',
  'font-src data: https:',
  'media-src data: blob: https:',
  "connect-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join('; ')

// Runs first: takes the one VS Code API handle and hands the bootstrap a transport.
const PRELUDE = `(function(){var api=acquireVsCodeApi();window.__ktTransport={post:function(m){api.postMessage(m)},accept:function(){return true}}})()`

function renderAppPanelHtml(document) {
  return `<meta http-equiv="Content-Security-Policy" content="${PANEL_POLICY}"><script>${PRELUDE}</script>${document}`
}

function byteLength(value) {
  return Buffer.byteLength(value, 'utf8')
}

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

// A message from the panel is relayed only in JSON-RPC bridge shape and bounded size.
function bridgeMessage(value) {
  if (!isPlainObject(value) || value.jsonrpc !== '2.0') return null
  if (value.method !== undefined && !(typeof value.method === 'string' && METHOD.test(value.method))) return null
  let text
  try {
    text = JSON.stringify(value)
  } catch {
    return null
  }
  if (byteLength(text) > MAX_MESSAGE_BYTES) return null
  return JSON.parse(text)
}

function validAppMessage(message) {
  const fields = Object.keys(message)
  const only = (allowed) => fields.every((field) => allowed.includes(field))
  switch (message.type) {
    case 'app.document':
      return only(['type', 'requestId', 'path']) && canonicalArtifactPath(message.path) !== null
    case 'app.open':
      return (
        only(['type', 'requestId', 'appId', 'title', 'html']) &&
        APP_ID.test(message.appId || '') &&
        typeof message.title === 'string' &&
        message.title.length <= MAX_TITLE_CHARS &&
        typeof message.html === 'string' &&
        byteLength(message.html) <= MAX_DOCUMENT_BYTES
      )
    case 'app.post':
      return only(['type', 'requestId', 'appId', 'message']) && APP_ID.test(message.appId || '') && bridgeMessage(message.message) !== null
    case 'app.close':
      return only(['type', 'requestId', 'appId']) && APP_ID.test(message.appId || '')
    default:
      return false
  }
}

class AppPanels {
  /**
   * ``createPanel(title)`` returns a VS Code WebviewPanel; ``confirmSend({title, name})``
   * resolves ``'send'``, ``'always'`` or null; ``post`` reaches the chat webview.
   */
  constructor({ createPanel, confirmSend, post }) {
    this.createPanel = createPanel
    this.confirmSend = confirmSend
    this.post = post
    this.panels = new Map()
  }

  async handle(message, runtime) {
    switch (message.type) {
      case 'app.document': {
        const route = canonicalArtifactPath(message.path)
        if (!route || !runtime) throw Error('Unknown app document')
        const html = await runtime.client.artifactText(route, MAX_DOCUMENT_BYTES)
        return { html }
      }
      case 'app.open':
        this.open(message.appId, message.title || 'App', message.html)
        return { opened: true }
      case 'app.post': {
        const entry = this.panels.get(message.appId)
        if (!entry) throw Error('The app is closed')
        await entry.panel.webview.postMessage(bridgeMessage(message.message))
        return { posted: true }
      }
      case 'app.close':
        this.panels.get(message.appId)?.panel.dispose()
        return { closed: true }
      default:
        throw Error('Unsupported app request')
    }
  }

  open(appId, title, document) {
    const existing = this.panels.get(appId)
    if (existing) {
      existing.title = title
      existing.panel.title = title
      if (existing.document !== document) {
        existing.document = document
        existing.panel.webview.html = renderAppPanelHtml(document)
      }
      existing.panel.reveal()
      return
    }
    const panel = this.createPanel(title)
    const entry = { panel, title, document, always: false, subscriptions: [] }
    this.panels.set(appId, entry)
    entry.subscriptions.push(
      panel.webview.onDidReceiveMessage((raw) => this.fromPanel(appId, entry, raw)),
      panel.onDidDispose(() => {
        for (const subscription of entry.subscriptions) subscription.dispose?.()
        if (this.panels.get(appId) === entry) this.panels.delete(appId)
        this.post({ type: 'app.closed', appId })
      }),
    )
    panel.webview.html = renderAppPanelHtml(document)
  }

  async fromPanel(appId, entry, raw) {
    const message = bridgeMessage(raw)
    if (!message || this.panels.get(appId) !== entry) return
    if (message.method !== 'kt/send') {
      this.post({ type: 'app.message', appId, message })
      return
    }
    const name = typeof message.params?.name === 'string' ? message.params.name.slice(0, 64) : ''
    let approved = entry.always
    if (!approved) {
      const answer = await this.confirmSend({ title: entry.title, name })
      if (answer === 'always') entry.always = true
      approved = answer === 'send' || answer === 'always'
    }
    if (this.panels.get(appId) !== entry) return
    if (!approved) {
      if (message.id !== undefined && message.id !== null)
        entry.panel.webview.postMessage({ jsonrpc: '2.0', id: message.id, error: { message: 'the user did not send it' } })
      return
    }
    this.post({ type: 'app.message', appId, message, interacted: true })
  }

  dispose() {
    for (const entry of [...this.panels.values()]) entry.panel.dispose()
    this.panels.clear()
  }
}

module.exports = { APP_TYPES, AppPanels, PANEL_POLICY, bridgeMessage, renderAppPanelHtml, validAppMessage }
