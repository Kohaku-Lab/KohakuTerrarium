import { providePlatformLinkOpener } from '@kohakuterrarium/chat-ui'
import { ElMessage } from 'element-plus'

import { useI18n } from '@/utils/i18n'
import { classifyFailure, isOpenReady } from './platformLinkPolicy.mjs'

// Host platform link opener for the VS Code webview. The shared card/Markdown
// leaves call the installed opener with the raw model-authored reference on a
// USER CLICK; this forwards it to the Host's narrow ``platform.openLink``
// operation — which resolves it against the live backend URL and calls
// ``vscode.env.openExternal`` — and surfaces a failure with the real shared
// dictionary (``chat.link.*``) through the webview notification surface.
//
// Installed from inside the App setup (``createViewRenderers``) so every
// descendant — the shared ConversationMessage/UIEventBlock and each
// MarkdownRenderer — injects it before it renders a link.
export function installPlatformLinkOpener({ request, getReadyId }) {
  const { t } = useI18n()
  const open = (target) => {
    // Capture the ready epoch THIS click owns. Binding the request to the epoch
    // at click time (rather than re-reading it when the Host answers) is what
    // lets a stale settlement be told apart from a genuine failure.
    const ownerReadyId = getReadyId()
    if (!isOpenReady(ownerReadyId)) {
      // No live ready epoch: the Host cannot resolve a link for this document and
      // would drop an epoch-less envelope. Report the same localized
      // "unavailable" surface a card the host cannot resolve shows, immediately.
      ElMessage.error({ message: t('chat.link.unavailable'), duration: 5000 })
      return
    }
    request('platform.openLink', { target, readyId: ownerReadyId }).catch(() => {
      // A failure that arrives after the ready epoch moved on is suppressed: the
      // open may already have happened under the previous owner, so this
      // document must never claim the browser did not open.
      if (classifyFailure({ ownerReadyId, currentReadyId: getReadyId() }) === 'stale') return
      ElMessage.error({ message: t('chat.link.openFailed'), duration: 5000 })
    })
  }
  providePlatformLinkOpener(open)
  return open
}
