/** Send the text of an inline <ui> button or form as a normal user message. */

import { ElMessage } from "element-plus"

/** Send ``text`` through ``chat.send``; a leading ``/`` is refused so a click never runs a command. */
export async function sayToChat(chat, text, t) {
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
    await chat.send(message)
    return true
  } catch (err) {
    ElMessage.error(err?.message || String(err))
    return false
  }
}
