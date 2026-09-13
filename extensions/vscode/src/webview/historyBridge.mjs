// Installs the Host request facades the shared API shim calls. Every
// delegate is scoped to this webview realm and revoked with the app so a
// disposed view can never silently resolve a stale read.
export function installHistoryBridge({ request }) {
  const history = (session, creature) => request('http.history', { session, creature })
  const historyPage = (session, creature, options = {}) => request('http.historyPage', { session, creature, options })
  const historyDetail = (session, creature, params = {}) => request('http.historyDetail', { session, creature, params })
  const interrupt = (session, creature) => request('http.interrupt', { session, creature })

  globalThis.__ktVsCodeHistory = history
  globalThis.__ktVsCodeHistoryPage = historyPage
  globalThis.__ktVsCodeHistoryDetail = historyDetail
  globalThis.__ktVsCodeInterrupt = interrupt

  return () => {
    if (globalThis.__ktVsCodeHistory === history) delete globalThis.__ktVsCodeHistory
    if (globalThis.__ktVsCodeHistoryPage === historyPage) delete globalThis.__ktVsCodeHistoryPage
    if (globalThis.__ktVsCodeHistoryDetail === historyDetail) delete globalThis.__ktVsCodeHistoryDetail
    if (globalThis.__ktVsCodeInterrupt === interrupt) delete globalThis.__ktVsCodeInterrupt
  }
}
