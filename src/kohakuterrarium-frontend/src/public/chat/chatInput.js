/** Return whether a composer key event should submit instead of inserting a newline. */
export function shouldSendOnEnter(event, { isCompact = false, isTouch = false } = {}) {
  if (event.isComposing || event.keyCode === 229 || event.key !== "Enter") return false
  if (isCompact || isTouch) return false
  return !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey
}
