// Test-only Vite entry: bundles the REAL transcript paging composable so its
// scroll scheduler/canceller pairing can be exercised without booting the
// whole webview. Built by test/transcriptPaging.test.cjs through the
// Extension Vite config, so the `@kohakuterrarium/chat-ui` boundary resolves
// exactly as production does.
import { ref } from 'vue'

import { useTranscriptPaging } from '../../src/webview/transcriptPaging.mjs'

export function createPaging(viewport, overrides = {}) {
  const messages = ref(overrides.messages || [{ id: 'm-0' }])
  const tab = ref('alpha')
  const chat = {
    _instanceGeneration: 1,
    historyPageByTab: overrides.historyPageByTab || {},
    tokenUsage: overrides.tokenUsage || {},
    processingByTab: overrides.processingByTab || {},
  }
  return useTranscriptPaging({
    chat,
    tab,
    messages,
    getIdentity: () => 'session-a:alpha',
    getViewport: () => viewport,
  })
}
