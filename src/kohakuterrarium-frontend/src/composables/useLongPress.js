/**
 * useLongPress — unified long-press → context-menu helper.
 *
 * Replaces right-click context menus with a touch-friendly long-press
 * gesture that fires after the configured delay (default 500ms).
 * Movement beyond a small threshold cancels the press so the user can
 * scroll without accidentally invoking the menu.
 *
 * Usage in a component:
 *
 *     const { onPointerDown, onPointerMove, onPointerUp, onPointerCancel } =
 *         useLongPress(() => openContextMenu())
 *     <div @pointerdown="onPointerDown" @pointermove="onPointerMove"
 *          @pointerup="onPointerUp" @pointercancel="onPointerCancel">
 *
 * Works with PointerEvent (which the WebView fires on touch), so we
 * don't need separate touch+mouse handlers.
 */

export function useLongPress(callback, options = {}) {
  const delay = options.delay ?? 500
  const moveThreshold = options.moveThreshold ?? 8 // pixels

  let timerId = null
  let startX = 0
  let startY = 0

  function _cancel() {
    if (timerId !== null) {
      clearTimeout(timerId)
      timerId = null
    }
  }

  function onPointerDown(event) {
    startX = event.clientX
    startY = event.clientY
    _cancel()
    timerId = setTimeout(() => {
      timerId = null
      try {
        callback(event)
      } catch (_err) {
        // Don't propagate — the gesture's job is to fire, the
        // caller's job is to handle.  Re-throwing here would mask
        // the gesture as a JS error in the WebView.
      }
    }, delay)
  }

  function onPointerMove(event) {
    if (timerId === null) return
    const dx = event.clientX - startX
    const dy = event.clientY - startY
    if (dx * dx + dy * dy > moveThreshold * moveThreshold) {
      _cancel()
    }
  }

  function onPointerUp() {
    _cancel()
  }

  function onPointerCancel() {
    _cancel()
  }

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
  }
}
