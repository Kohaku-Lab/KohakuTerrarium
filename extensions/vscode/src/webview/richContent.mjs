import { AppLauncher, registerWidgetRenderer, setMermaidStyleNonce, toolArgsProgressLabel } from '@kohakuterrarium/chat-ui'

// A webview cannot run app frames (its nonce CSP reaches every nested frame), so
// a ``kt.app`` widget or an inline ``html`` block opens in its own editor tab
// (``openApp`` below, Host side in ``host/appPanels.cjs``). Mermaid's SVG styles
// carry the page's style-only nonce.
export function installRichContent(document) {
  registerWidgetRenderer('kt.app', AppLauncher, {
    key: 'vscode',
    trust: 'sandboxed',
    surfaces: ['chat'],
    summary: (widget) => widget.title || 'App',
    icon: () => 'i-carbon-application-web',
  })
  setMermaidStyleNonce(document.getElementById('app')?.dataset.styleNonce || '')
}

/** The ``openApp`` message action: apps in editor tabs over the fixed ``app.*`` Host requests. */
export function createAppOpener(request, window) {
  const apps = new Map()
  window.addEventListener('message', ({ data }) => {
    if (data?.type === 'app.message') apps.get(data.appId)?.onMessage(data.message, data.interacted === true)
    else if (data?.type === 'app.closed') {
      const app = apps.get(data.appId)
      apps.delete(data.appId)
      app?.onClose()
    }
  })
  return {
    fetchDocument: async (path) => (await request('app.document', { path })).html,
    async open({ appId, title, document }, handlers) {
      apps.set(appId, handlers)
      await request('app.open', { appId, title, html: document })
      return {
        post: (message) => request('app.post', { appId, message }).catch(() => {}),
        close: () => request('app.close', { appId }).catch(() => {}),
      }
    },
  }
}

// English status text for a streaming tool call, like the view's other labels.
const progressText = (key, { size, tool }) => (key === 'chat.writingApp' ? `Writing app… ${size}` : `Writing ${tool} arguments… ${size}`)

/** The processing line while a tool call's arguments stream in ``tab``, or null. */
export function processingLabel(chat, tab) {
  return toolArgsProgressLabel(chat.toolArgsProgressForTab?.(tab), progressText)
}

/** Send a message an inline ``<ui>`` button builds through the composer's accepted transport. */
export function inlineSend(chat, hostAcceptedChat) {
  return (text) => {
    if (chat.wsStatus !== 'open') throw Error('Chat is disconnected. Press Refresh Sessions and try again.')
    return hostAcceptedChat.send(text)
  }
}
