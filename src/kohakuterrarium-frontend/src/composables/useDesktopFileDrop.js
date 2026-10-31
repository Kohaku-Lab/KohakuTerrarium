import { onBeforeUnmount, onMounted } from "vue"

/** Page event the desktop shell sends while files are dragged over its window. */
export const FILE_DROP_EVENT = "kt:desktop-file-drop"

/** Text that refers to dropped files: one path per line, quoted so spaces survive. */
export function formatDroppedPaths(paths) {
  return paths
    .filter((path) => typeof path === "string" && path)
    .map((path) => (path.includes("`") ? JSON.stringify(path) : `\`${path}\``))
    .join("\n")
}

/** Append dropped paths to a draft on their own lines. */
export function appendDroppedPaths(draft, paths) {
  const added = formatDroppedPaths(paths)
  if (!added) return draft
  if (!draft) return added
  return `${draft}${draft.endsWith("\n") ? "" : "\n"}${added}`
}

function insideElement(element, x, y) {
  const rect = element?.getBoundingClientRect?.()
  if (!rect) return false
  return x >= rect.left && x < rect.right && y >= rect.top && y < rect.bottom
}

/**
 * React to files dragged over the desktop window while the pointer is over
 * ``target``: ``onHover(true|false)`` follows the pointer, ``onPaths(paths)``
 * receives the real file paths on release. Does nothing in a browser.
 */
export function useDesktopFileDrop(target, { enabled = () => true, onHover, onPaths }) {
  let hovering = false

  function setHover(value) {
    if (value === hovering) return
    hovering = value
    onHover?.(value)
  }

  function onEvent(event) {
    const { phase, paths = [], x = 0, y = 0 } = event.detail || {}
    if (phase === "leave" || !enabled()) {
      setHover(false)
      return
    }
    const ratio = window.devicePixelRatio || 1
    const over = insideElement(target.value, x / ratio, y / ratio)
    if (phase === "drop") {
      setHover(false)
      if (over && paths.length) onPaths?.(paths)
      return
    }
    setHover(over)
  }

  onMounted(() => window.addEventListener(FILE_DROP_EVENT, onEvent))
  onBeforeUnmount(() => {
    window.removeEventListener(FILE_DROP_EVENT, onEvent)
    setHover(false)
  })
}
