import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from "vue"

const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files")

/**
 * The "drop files to attach" hint of a drop zone: `dragOver` is true while a file drag is over
 * the zone, and `onFiles(fileList)` receives what is dropped on it. The hint also clears when a
 * child consumes the drop or the drag is cancelled, so it never outlives the drag.
 */
export function useFileDropHint(onFiles) {
  const dragOver = ref(false)
  let depth = 0

  function reset() {
    depth = 0
    dragOver.value = false
  }
  function onDragEnter(event) {
    if (!hasFiles(event)) return
    event.preventDefault()
    depth += 1
    dragOver.value = true
  }
  function onDragLeave(event) {
    if (!hasFiles(event)) return
    depth = Math.max(0, depth - 1)
    if (!depth) dragOver.value = false
  }
  function onDragOver(event) {
    if (hasFiles(event)) event.preventDefault()
  }
  function onDrop(event) {
    reset()
    if (!hasFiles(event)) return
    event.preventDefault()
    onFiles(event.dataTransfer?.files || [])
  }

  // Capture phase: runs even when a child (the composer) stops the drop, or no drop follows (Esc).
  const listen = () => {
    window.addEventListener("drop", reset, true)
    window.addEventListener("dragend", reset, true)
  }
  const unlisten = () => {
    window.removeEventListener("drop", reset, true)
    window.removeEventListener("dragend", reset, true)
    reset()
  }
  onMounted(listen)
  onActivated(listen)
  onDeactivated(unlisten)
  onBeforeUnmount(unlisten)

  return { dragOver, handlers: { onDragEnter, onDragLeave, onDragOver, onDrop } }
}
