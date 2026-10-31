/**
 * Native overlay windows.
 *
 * An overlay that declares ``surface: window`` is drawn in its own desktop
 * window once the user has allowed that for the overlay and the shell can
 * open one; otherwise it falls back to the in-app layer.
 */

import { watch } from "vue"

import {
  isOverlayVisible,
  isPlacementStorageKey,
  isWindowAllowed,
  overlays,
  reloadPlacements,
} from "@/ext/overlays"
import { closeOverlayWindow, openOverlayWindow, overlayWindowsSupported } from "@/utils/hostBridge"
import { makeWindowLabel } from "@/utils/windowLabel"

export const OVERLAY_PARAM = "kt_overlay"
export const WINDOW_LABEL_PREFIX = "overlay-"

/** The overlay key an overlay window was opened for, or ``null`` in the normal app. */
export function overlayKeyFromSearch(search) {
  return new URLSearchParams(search || "").get(OVERLAY_PARAM) || null
}

/** Window label for an overlay key: readable, unique per key, accepted by the shell. */
export function windowLabel(key) {
  return makeWindowLabel(WINDOW_LABEL_PREFIX, key)
}

export function wantsWindow(overlay) {
  return overlay?.options.surface === "window"
}

/** Whether this overlay is drawn in a native window instead of the app. */
export function windowActive(key) {
  const overlay = overlays.get(key)
  return wantsWindow(overlay) && isWindowAllowed(key) && overlayWindowsSupported()
}

function spec(overlay) {
  const { options } = overlay
  return {
    label: windowLabel(overlay.key),
    key: overlay.key,
    anchor: options.anchor || "bottom-right",
    width: options.width || 320,
    height: options.height || 240,
    alwaysOnTop: options.alwaysOnTop !== false,
  }
}

const open = new Set()

/** Open the windows that should exist and close the ones that should not. */
export function syncOverlayWindows() {
  const wanted = new Set()
  for (const overlay of overlays.values()) {
    if (!windowActive(overlay.key) || !isOverlayVisible(overlay.key)) continue
    const label = windowLabel(overlay.key)
    wanted.add(label)
    if (!open.has(label)) {
      open.add(label)
      openOverlayWindow(spec(overlay))
    }
  }
  for (const label of [...open]) {
    if (wanted.has(label)) continue
    open.delete(label)
    closeOverlayWindow(label)
  }
}

let _started = false

/** Keep native overlay windows in step with the registry, visibility and consent. Idempotent. */
export function startOverlayWindows() {
  if (_started || typeof window === "undefined") return
  _started = true
  watch(
    () =>
      [...overlays.values()]
        .map((o) => `${o.key}|${windowActive(o.key)}|${isOverlayVisible(o.key)}`)
        .join(","),
    syncOverlayWindows,
    { immediate: true },
  )
  // An overlay window hides itself by writing the shared placements.
  window.addEventListener("storage", (event) => {
    if (isPlacementStorageKey(event.key)) reloadPlacements()
  })
}
