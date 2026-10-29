/** Send the text of an inline <ui> button or form as a normal user message. */

import { ElMessage } from "element-plus"

/**
 * Send ``text`` through ``send`` (default ``chat.send``); a leading ``/`` is
 * refused so a click never runs a command. A host passes its own ``send``
 * when user messages go through a host transport (the VS Code webview).
 */
export async function sayToChat(chat, text, t, send = null) {
  const message = String(text || "").trim()
  if (!message) return false
  if (message.startsWith("/")) {
    ElMessage.warning(
      t
        ? t("chat.inlineUi.commandRefused")
        : "This message looks like a command, so it was not sent.",
    )
    return false
  }
  try {
    await (typeof send === "function" ? send(message) : chat.send(message))
    return true
  } catch (err) {
    ElMessage.error(err?.message || String(err))
    return false
  }
}
