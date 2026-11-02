import { showActionNotification } from "@/utils/hostBridge"

/** Page event the shell raises when a notification button (or its body) is pressed. */
export const ACTION_EVENT = "kt:desktop-notification-action"
const MAX_WAITING = 20

const waiting = new Map()
let listening = false
let counter = 0

function onAction(event) {
  const { token, id } = event.detail || {}
  const choose = waiting.get(token)
  if (!choose) return
  waiting.delete(token)
  choose(id ?? null)
}

/**
 * Show a notification with `actions` (`[{ id, label }]`) and call `onChoose(id)` when a button is
 * pressed, or `onChoose(null)` when the notification itself is clicked. Resolves ``false`` when the
 * shell cannot show buttons, so the caller can fall back to a plain notification.
 */
export async function showActionToast({ title, body, actions }, onChoose) {
  if (!listening) {
    window.addEventListener(ACTION_EVENT, onAction)
    listening = true
  }
  counter += 1
  const token = `n${Date.now().toString(36)}-${counter}`
  waiting.set(token, onChoose)
  while (waiting.size > MAX_WAITING) waiting.delete(waiting.keys().next().value)
  const shown = await showActionNotification(title, body, actions, token)
  if (!shown) waiting.delete(token)
  return shown
}
