// Installs the Host request facades the shared sub-agent leaves call. Each named
// delegate maps to one fixed Host route (never a generic URL/method proxy) and is
// scoped to this webview realm and revoked with the app, so a disposed view can
// never silently resolve a stale read or a stale send. The saved surface is
// read-only: it exposes discovery and a conversation read, never a send.
export function installSubagentBridge({ request }) {
  const conversation = (session, creature, options = {}) => request('http.subagentConversation', { session, creature, options })
  const send = (session, creature, name, content, jobId) =>
    request('http.subagentSend', { session, creature, name, content, ...(jobId ? { jobId } : {}) })
  const listRuns = (session, options = {}) => request('http.subagentList', { session, options })
  const savedConversation = (session, options = {}) => request('http.subagentSavedConversation', { session, options })
  const promote = (session, creature, jobId) => request('http.promote', { session, creature, jobId })

  globalThis.__ktVsCodeSubagentConversation = conversation
  globalThis.__ktVsCodeSubagentSend = send
  globalThis.__ktVsCodeSubagentList = listRuns
  globalThis.__ktVsCodeSavedSubagentConversation = savedConversation
  globalThis.__ktVsCodePromote = promote

  return () => {
    if (globalThis.__ktVsCodeSubagentConversation === conversation) delete globalThis.__ktVsCodeSubagentConversation
    if (globalThis.__ktVsCodeSubagentSend === send) delete globalThis.__ktVsCodeSubagentSend
    if (globalThis.__ktVsCodeSubagentList === listRuns) delete globalThis.__ktVsCodeSubagentList
    if (globalThis.__ktVsCodeSavedSubagentConversation === savedConversation) delete globalThis.__ktVsCodeSavedSubagentConversation
    if (globalThis.__ktVsCodePromote === promote) delete globalThis.__ktVsCodePromote
  }
}
