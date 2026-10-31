/** Key names the desktop shell's global shortcut parser accepts, by ``KeyboardEvent.code``. */
const NAMED_KEYS = {
  Space: "Space",
  Enter: "Enter",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
}

export function isMacPlatform() {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || "")
}

function keyName(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3)
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code
  return NAMED_KEYS[code] || ""
}

/**
 * The accelerator text the shell registers for a key press, e.g.
 * ``CommandOrControl+Shift+Space``, or ``""`` when the press is not a usable
 * system-wide shortcut (no modifier, or a key the shell cannot bind).
 */
export function acceleratorFromEvent(event, mac = isMacPlatform()) {
  const key = keyName(String(event.code || ""))
  if (!key) return ""
  const modifiers = []
  if (event.ctrlKey) modifiers.push(mac ? "Control" : "CommandOrControl")
  if (event.metaKey) modifiers.push(mac ? "CommandOrControl" : "Super")
  if (event.altKey) modifiers.push("Alt")
  if (event.shiftKey) modifiers.push("Shift")
  return modifiers.length ? [...modifiers, key].join("+") : ""
}

/** A shortcut as a person reads it: ``Ctrl+Shift+Space`` (``⌘`` on a Mac). */
export function displayAccelerator(accelerator, mac = isMacPlatform()) {
  return String(accelerator || "")
    .split("+")
    .map((part) => {
      switch (part) {
        case "CommandOrControl":
          return mac ? "⌘" : "Ctrl"
        case "Control":
          return mac ? "⌃" : "Ctrl"
        case "Super":
          return mac ? "⌘" : "Win"
        case "Alt":
          return mac ? "⌥" : "Alt"
        default:
          return part
      }
    })
    .join("+")
}
