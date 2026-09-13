/**
 * The one slash-menu keyboard policy both hosts drive; returns whether the event
 * was consumed (so it must not reach the composer's submit-on-Enter path).
 */
export function handleSlashKeydown(
  event,
  { open = false, entries = [], selectedIndex = 0, move, choose, dismiss } = {},
) {
  // IME composition and already-prevented events belong to the composer/browser:
  // consume them so submit cannot also fire, but choose/dismiss/move nothing.
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return true
  if (!open) return false
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault()
    move(event.key === "ArrowDown" ? 1 : -1)
    return true
  }
  if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
    const selected = entries[selectedIndex]
    if (selected) {
      event.preventDefault()
      choose(selected)
      return true
    }
  }
  if (event.key === "Escape") {
    event.preventDefault()
    event.stopPropagation()
    dismiss()
    return true
  }
  return false
}
