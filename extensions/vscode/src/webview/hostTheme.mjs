// Host theme seam. The shared chat-ui leaves style themselves with UnoCSS
// ``dark:`` variants, which compile to a ``.dark`` ancestor selector. The
// Dashboard toggles ``html.dark`` through its theme store; the VS Code webview
// owns no theme preference and instead mirrors the host. VS Code stamps
// ``vscode-dark`` / ``vscode-high-contrast`` (dark) or ``vscode-light`` /
// ``vscode-high-contrast-light`` onto ``<body>``; we mirror that onto ``<html>``
// so the shared utilities actually apply, and keep observing for the life of the
// app so a host theme switch is reflected without a reload.
const HIGH_CONTRAST_LIGHT = 'vscode-high-contrast-light'
const DARK_BODY_CLASSES = ['vscode-dark', 'vscode-high-contrast']

// High-contrast light also begins with ``vscode-high-contrast``, so reject it
// before the dark list: VS Code emits both conventions on the same <body>.
export function isHostDark(body) {
  const classes = body?.classList
  if (!classes) return false
  if (classes.contains(HIGH_CONTRAST_LIGHT)) return false
  return DARK_BODY_CLASSES.some((name) => classes.contains(name))
}

export function installHostTheme({ document: doc = globalThis.document } = {}) {
  const root = doc?.documentElement
  const body = doc?.body
  const Observer = doc?.defaultView?.MutationObserver ?? globalThis.MutationObserver
  if (!root) return () => {}
  const apply = () => root.classList.toggle('dark', isHostDark(body))
  apply()
  if (!body || typeof Observer !== 'function') return () => {}
  const observer = new Observer(apply)
  observer.observe(body, { attributes: true, attributeFilter: ['class'] })
  // Disconnect only; a disposed webview must never write to <html> again.
  return () => observer.disconnect()
}
