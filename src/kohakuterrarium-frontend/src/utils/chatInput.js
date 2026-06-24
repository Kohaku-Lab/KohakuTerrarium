/**
 * Chat composer keyboard helpers.
 *
 * Extracted from ``ChatPanel.vue`` so the Enter-to-send decision is a
 * pure, unit-testable function (the component just wires DOM events +
 * the density flag into it).
 */

/**
 * Decide whether an Enter keydown in the chat composer should SEND the
 * message, as opposed to inserting a newline.
 *
 * Rules:
 *  - During IME composition (Chinese/Japanese/Korean), never send —
 *    Enter confirms the highlighted candidate, not the message.
 *  - On the touch / compact shell, never send on Enter — mobile soft
 *    keyboards have no Shift+Enter, and the on-screen return key would
 *    otherwise fire-and-send every line (notably iOS Safari). The
 *    on-screen send button is the send affordance there.
 *  - Otherwise, plain Enter sends; any modifier (Shift/Ctrl/Cmd/Alt)
 *    inserts a newline.
 *
 * @param {KeyboardEvent} e
 * @param {{ isCompact?: boolean }} [opts]
 * @returns {boolean}
 */
export function shouldSendOnEnter(e, { isCompact = false } = {}) {
  if (e.isComposing || e.keyCode === 229) return false
  if (e.key !== "Enter") return false
  if (isCompact) return false
  return !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey
}
