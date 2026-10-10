/**
 * A panel width the user drags with a grip, kept within [min, max] and
 * remembered under `key`. `edge` is the side of the panel the grip sits on:
 * "right" grows the panel as the pointer moves right, "left" as it moves
 * left. `reset` restores `initial`.
 */

import { onBeforeUnmount, ref } from "vue"

import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

export function useResizableWidth({ key, min, max, initial, edge = "right" }) {
  const clamp = (v) => Math.max(min, Math.min(max, v))
  const width = ref(clamp(Number(readLocalPref(key)) || initial))
  const dragging = ref(false)
  let stopDrag = () => {}

  function save() {
    writeLocalPref(key, String(Math.round(width.value)))
  }

  function reset() {
    width.value = clamp(initial)
    save()
  }

  /** Pointer-down handler for a grip whose parent element is the panel. */
  function startDrag(e) {
    const grip = e.currentTarget
    const rect = grip.parentElement.getBoundingClientRect()
    e.preventDefault()
    grip.setPointerCapture?.(e.pointerId)
    dragging.value = true
    const onMove = (ev) => {
      width.value = clamp(edge === "left" ? rect.right - ev.clientX : ev.clientX - rect.left)
    }
    const onUp = () => {
      dragging.value = false
      save()
      stopDrag()
    }
    stopDrag = () => {
      grip.releasePointerCapture?.(e.pointerId)
      document.removeEventListener("pointermove", onMove)
      document.removeEventListener("pointerup", onUp)
      document.removeEventListener("pointercancel", onUp)
      stopDrag = () => {}
    }
    document.addEventListener("pointermove", onMove)
    document.addEventListener("pointerup", onUp)
    document.addEventListener("pointercancel", onUp)
  }

  onBeforeUnmount(() => stopDrag())

  return { width, dragging, startDrag, reset }
}
