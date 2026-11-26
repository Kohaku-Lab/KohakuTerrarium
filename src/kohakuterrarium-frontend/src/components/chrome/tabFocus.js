const PREVIOUS = new Set(["ArrowLeft", "ArrowUp"])
const NEXT = new Set(["ArrowRight", "ArrowDown"])

/**
 * Keydown handler for a `role="tablist"`: arrow keys move focus to the
 * previous / next tab (wrapping), Home / End to the first / last. Focus only
 * moves; Enter or Space on the focused tab selects it.
 */
export function moveTabFocus(event) {
  const tabs = Array.from(event.currentTarget.querySelectorAll('[role="tab"]')).filter(
    (tab) => !tab.disabled,
  )
  const at = tabs.indexOf(event.target.closest?.('[role="tab"]'))
  if (!tabs.length || at < 0) return
  let next
  if (PREVIOUS.has(event.key)) next = (at - 1 + tabs.length) % tabs.length
  else if (NEXT.has(event.key)) next = (at + 1) % tabs.length
  else if (event.key === "Home") next = 0
  else if (event.key === "End") next = tabs.length - 1
  else return
  event.preventDefault()
  tabs[next].focus()
}
