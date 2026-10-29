// The ``mermaid`` module for the webview bundle. The bundle is one IIFE, so a
// real ``import("mermaid")`` would be inlined and run at start-up; instead the
// Extension ships Mermaid's own browser build as ``dist/mermaid.js`` and this
// stand-in loads it with the page's script nonce on the first diagram.
const scriptNonce = globalThis.document?.currentScript?.nonce || ''

let loading = null

function load() {
  loading ??= new Promise((resolve, reject) => {
    const src = globalThis.document?.getElementById('app')?.dataset.mermaidUri
    if (!src) {
      reject(Error('Diagrams are unavailable in this view'))
      return
    }
    const script = globalThis.document.createElement('script')
    script.src = src
    script.nonce = scriptNonce
    script.onload = () => (globalThis.mermaid ? resolve(globalThis.mermaid) : reject(Error('Mermaid did not load')))
    script.onerror = () => reject(Error('Mermaid did not load'))
    globalThis.document.head.append(script)
  }).catch((error) => {
    loading = null
    throw error
  })
  return loading
}

let config = null

export default {
  initialize(options) {
    config = options
  },
  async render(id, source) {
    const mermaid = await load()
    if (config) mermaid.initialize(config)
    return mermaid.render(id, source)
  },
}
