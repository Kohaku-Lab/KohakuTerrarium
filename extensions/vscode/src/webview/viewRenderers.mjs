import { providePlatformOrigin } from '@kohakuterrarium/chat-ui'
import { h } from 'vue'

import { installPlatformLinkOpener } from './platformLinkOpener.mjs'
import { createSessionRenderers } from './sessionRenderers.mjs'

export function createViewRenderers({
  MessageRow,
  MarkdownRenderer,
  available,
  busy,
  currentSession,
  openSession,
  resumeSession,
  historyDetail,
  request,
  getReadyId,
}) {
  // Explicit, host-neutral platform origin for the shared UI-event Markdown
  // links. The webview's document origin is the opaque ``vscode-webview://`` and
  // there is no backend HTTP origin behind the bridge, so an explicit ``null`` is
  // installed: a card link must be treated as external, never rewritten against
  // the fake webview origin. Installed here because ``createViewRenderers`` runs
  // inside the App setup, so descendants (the shared ConversationMessage/MessageRow
  // and its UIEventBlock) receive it.
  providePlatformOrigin(null)
  // The matching link-opener seam: a card/Markdown link click is forwarded to the
  // Host's ``platform.openLink`` operation and never navigates the webview.
  installPlatformLinkOpener({ request, getReadyId })
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

  function renderTranscriptMessage(message, context = {}) {
    // Message actions receive the full-projection index and the owning pane's tab.
    const body = h(MessageRow, {
      message,
      prevMessage: context.previousMessage ?? null,
      isFirst: !!context.isFirst,
      messageIdx: context.absoluteIndex ?? null,
      isLastAssistant: !!context.isLastAssistant,
      tabId: currentSession.value?.target || '',
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
