import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { installHostTheme } from './hostTheme.mjs'

// Coherent boot seam for the webview entry. One place builds the app shell with
// pinia, mirrors the VS Code host theme onto ``html.dark`` (installed before
// mount so the first paint is already themed), and mounts. The theme observer is
// torn down with the app, so a disposed webview never writes again.
export function bootWebview(App, { document: doc = globalThis.document, mount = '#app' } = {}) {
  const app = createApp(App)
  app.use(createPinia())
  app.onUnmount(installHostTheme({ document: doc }))
  return app.mount(doc.querySelector(mount))
}
