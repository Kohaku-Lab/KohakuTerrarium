import { CommandResultMessage } from '@kohakuterrarium/chat-ui'
import { h } from 'vue'

import { createSessionRenderers } from './sessionRenderers.mjs'

export function createViewRenderers({
  ConversationMessage,
  MarkdownRenderer,
  available,
  busy,
  currentSession,
  openSession,
  resumeSession,
  historyDetail,
}) {
  const { actionButton, icon, renderSession } = createSessionRenderers({ available, busy, currentSession, openSession, resumeSession })

  function renderSharedText(content, breaks = false) {
    return h(MarkdownRenderer, { content, breaks })
  }

  // A truncated channel/record row carries opaque detail keys. The full-body
  // read is a safe, ownership-fenced store action; only render the control
  // when the store marked the row truncated, so a preview never silently
  // pretends to be complete.
  function renderDetailControls(message) {
    if (!historyDetail) return []
    return (message._historyDetails || []).map((key) =>
      h(
        'button',
        {
          key: `history-detail:${key}`,
          type: 'button',
          class: 'history-detail self-center',
          'data-history-detail': key,
          disabled: historyDetail.pending.value !== null,
          onClick: () => historyDetail.load(key),
        },
        'Show full message',
      ),
    )
  }

  function renderTranscriptMessage(message, { reply }) {
    if (message?.role === 'command_result') return h(CommandResultMessage, { message })
    // Media inside a shared message resolves through the injected media resolver
    // (browser direct URL or Host-spooled webview URI); no observer wrapper is
    // needed here anymore.
    const body = h(ConversationMessage, {
      message,
      renderText: renderSharedText,
      onReply: ({ actionId, values }) => reply(actionId, values),
    })
    const details = renderDetailControls(message)
    // Preserve the rendered message as the row root so the paging row
    // selector keeps the message as its target; only wrap when a detail
    // control is attached.
    if (!details.length) return body
    return h('div', { class: 'transcript-row' }, [...details, body])
  }

  return {
    actionButton,
    icon,
    renderSession,
    renderSharedText,
    renderTranscriptMessage,
  }
}
