/** Native presentation and viewport fallback for registered overlays. */
import { nextTick, reactive, shallowRef, shallowReactive, watch } from "vue"

import {
  completeNativeReset,
  isOverlayVisible,
  isPlacementStorageKey,
  isWindowAllowed,
  overlays,
  placements,
  resetOverlay,
  windowPreference,
  reloadPlacements,
  isPetOverlay,
} from "@/ext/overlays"
import { extState } from "@/ext/state"
import {
  closeOverlayWindow,
  desktopCapabilities,
  openOverlayWindow,
  overlayWindowsSupported,
  showOverlayWindow,
  resetOverlayWindow,
} from "@/utils/hostBridge"
import { makeWindowLabel } from "@/utils/windowLabel"

export const OVERLAY_PARAM = "kt_overlay"
export const WINDOW_LABEL_PREFIX = "overlay-"
export const windowStates = reactive(new Map())
const capabilities = shallowRef(null)
const open = new Map()
const closing = shallowReactive(new Map())
let started = false
let generation = 0

export function overlayKeyFromSearch(search) {
  return new URLSearchParams(search || "").get(OVERLAY_PARAM) || null
}

export function windowLabel(key) {
  const params = new URLSearchParams(window.location.search)
  if (params.get(OVERLAY_PARAM) === key && params.get("kt_overlay_label"))
    return params.get("kt_overlay_label")
  const realm = requiresPresentation(overlays.get(key))
    ? `-${makeWindowLabel("", extState.hostKey).slice(-8)}`
    : ""
  return makeWindowLabel(WINDOW_LABEL_PREFIX, key + realm)
}

export function requiresPresentation(overlay) {
  return overlay?.options.desktopPet === true || overlay?.options.windowReady === true
}

export function wantsWindow(overlay) {
  return overlay?.options.surface === "window"
}

export function desktopPetSupported() {
  return (
    overlayWindowsSupported() &&
    capabilities.value?.overlayWindows === true &&
    capabilities.value?.overlayPointer === true &&
    capabilities.value?.overlayPresentation >= 1
  )
}

function desired(overlay) {
  const allowed = overlay?.options.desktopPet
    ? windowPreference(overlay.key) !== false && desktopPetSupported()
    : overlay?.options.firstParty === true || isWindowAllowed(overlay.key)
  const supported =
    (!overlay?.options.clickThrough || capabilities.value?.overlayPointer === true) &&
    (!overlay?.options.windowReady || capabilities.value?.overlayPresentation >= 1) &&
    (!overlay?.options.contentPlacement ||
      (capabilities.value?.overlayResize && capabilities.value?.overlayPlacementNative))
  return wantsWindow(overlay) && allowed && supported && overlayWindowsSupported()
}

export function windowActive(key) {
  const overlay = overlays.get(key)
  return (
    desired(overlay) &&
    (!requiresPresentation(overlay) ||
      ["presenting", "ready"].includes(windowStates.get(key)?.phase))
  )
}

export function windowFailure(key) {
  const overlay = overlays.get(key)
  if (isPetOverlay(overlay) && [...closing.values()].some((entry) => entry.pet && entry.failed))
    return "The previous desktop pet could not close. Retry to finish switching pets."
  if (!requiresPresentation(overlay) || windowPreference(key) === false) return ""
  if (
    overlay?.options.desktopPet &&
    overlayWindowsSupported() &&
    capabilities.value &&
    !desktopPetSupported()
  )
    return "Desktop pointer/presentation support is unavailable in this shell. Using in-app display."
  return windowStates.get(key)?.reason || ""
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
    ...(options.contentPlacement ? { contentPlacement: true } : {}),
  }
}

function retire(label, entry) {
  if (closing.has(label)) return closing.get(label).promise
  const record = { ...entry, failed: false }
  record.promise = entry.promise
    .then(() => closeOverlayWindow(label))
    .catch(() => false)
    .then((closed) => {
      if (closed) closing.delete(label)
      else closing.set(label, { ...record, failed: true })
      syncOverlayWindows()
      return closed
    })
  closing.set(label, record)
  return record.promise
}

export function petPresentationBlocked(key) {
  return (
    isPetOverlay(overlays.get(key)) &&
    ([...closing.values()].some((entry) => entry.pet) ||
      [...open.values()].some((entry) => entry.pet && entry.key !== key))
  )
}

function cancel(label, entry) {
  open.delete(label)
  clearTimeout(entry.timer)
  if (windowStates.get(entry.key)?.generation === entry.generation) windowStates.delete(entry.key)
  return retire(label, entry)
}

function fail(label, entry, reason) {
  if (open.get(label) !== entry) return
  clearTimeout(entry.timer)
  windowStates.set(entry.key, { phase: "failed", generation: entry.generation, reason })
  void retire(label, entry)
}

export async function receiveOverlayReady(event) {
  const { key, generation: id } = event.detail || {}
  const label = windowLabel(key)
  const entry = open.get(label)
  if (!entry || entry.generation !== id || windowStates.get(key)?.phase !== "opening") return
  if (!desired(overlays.get(key)) || !isOverlayVisible(key)) return
  windowStates.set(key, { phase: "presenting", generation: id })
  await nextTick()
  if (open.get(label) !== entry || !desired(overlays.get(key)) || !isOverlayVisible(key)) return
  if (placements[key]?.nativeReset) {
    const reset = await applyNativeReset(overlays.get(key), label)
    if (
      open.get(label) !== entry ||
      windowStates.get(key)?.phase !== "presenting" ||
      !desired(overlays.get(key)) ||
      !isOverlayVisible(key)
    )
      return
    if (!reset) {
      fail(label, entry, "Desktop position reset failed. Using in-app display; retry desktop.")
      return
    }
  }
  const ok = await showOverlayWindow(label, id)
  if (open.get(label) !== entry || windowStates.get(key)?.phase !== "presenting") return
  if (ok) {
    clearTimeout(entry.timer)
    windowStates.set(key, { phase: "ready", generation: id })
  } else
    fail(label, entry, "Desktop presentation failed. Using in-app display; retry or choose in-app.")
}

async function applyNativeReset(overlay, label) {
  const requested = placements[overlay.key]?.nativeReset
  if (!requested) return true
  const ok = await resetOverlayWindow({ ...spec(overlay), label })
  if (ok) completeNativeReset(overlay.key, requested)
  return ok
}

export function resetNativeOverlay(key) {
  const overlay = overlays.get(key)
  if (!overlay || !desired(overlay)) return false
  resetOverlay(key, true)
  if (isOverlayVisible(key) && windowActive(key)) void applyNativeReset(overlay, windowLabel(key))
  return true
}

export function retryOverlayWindow(key) {
  if (isPetOverlay(overlays.get(key))) {
    for (const [label, entry] of closing) {
      if (!entry.pet || !entry.failed) continue
      closing.delete(label)
      void retire(label, entry)
    }
  }
  const label = windowLabel(key)
  const entry = open.get(label)
  if (entry) cancel(label, entry)
  windowStates.delete(key)
  if (!entry) syncOverlayWindows()
}

/** Track decoded viewport content for the current overlay registration. */
export function setOverlayContentReady(overlay, available) {
  if (!requiresPresentation(overlay) || overlays.get(overlay.key) !== overlay) return
  overlay.contentReady = Boolean(available)
  syncOverlayWindows()
}

/** Reconcile singleton native windows, preserving a viewport renderer until readiness. */
export function syncOverlayWindows() {
  for (const [label, entry] of open) {
    const overlay = overlays.get(entry.key)
    if (
      !overlay ||
      !desired(overlay) ||
      !isOverlayVisible(entry.key) ||
      label !== windowLabel(entry.key)
    )
      cancel(label, entry)
  }
  const wanted = new Set()
  for (const overlay of overlays.values()) {
    if (!desired(overlay) || !isOverlayVisible(overlay.key)) continue
    const label = windowLabel(overlay.key)
    if (
      requiresPresentation(overlay) &&
      !overlay.contentReady &&
      (!open.has(label) || windowStates.get(overlay.key)?.phase === "opening")
    )
      continue
    wanted.add(label)
    if (open.has(label) || closing.has(label) || petPresentationBlocked(overlay.key)) continue
    const request = spec(overlay)
    const entry = {
      key: overlay.key,
      pet: isPetOverlay(overlay),
      generation: null,
      timer: null,
      promise: null,
    }
    if (requiresPresentation(overlay)) {
      entry.generation = `${Date.now()}-${++generation}`
      request.generation = entry.generation
      request.hostKey = extState.hostKey
      windowStates.set(overlay.key, { phase: "opening", generation: entry.generation })
    }
    open.set(label, entry)
    entry.promise = openOverlayWindow(request)
    if (!entry.generation)
      entry.promise.then((ok) => {
        if (ok && open.get(label) === entry) void applyNativeReset(overlay, label)
      })
    if (entry.generation) {
      entry.timer = setTimeout(
        () =>
          fail(
            label,
            entry,
            "Desktop pet did not become ready. Using in-app display; retry or choose in-app.",
          ),
        10000,
      )
      entry.promise.then((ok) => {
        if (!ok)
          fail(
            label,
            entry,
            "Could not open the desktop pet. Using in-app display; retry or choose in-app.",
          )
      })
    }
  }
  for (const [label, entry] of open) {
    if (!wanted.has(label)) cancel(label, entry)
  }
}

export async function refreshOverlayCapabilities() {
  capabilities.value = await desktopCapabilities({ refresh: true })
  syncOverlayWindows()
}

export function startOverlayWindows() {
  if (started || typeof window === "undefined") return
  started = true
  window.addEventListener("kt:overlay-ready", receiveOverlayReady)
  window.addEventListener("kt:overlay-closed", (event) => {
    const { key, generation: id } = event.detail || {}
    const label = windowLabel(key)
    const entry = open.get(label)
    if (
      entry?.generation === id &&
      ["opening", "presenting", "ready"].includes(windowStates.get(key)?.phase)
    )
      fail(label, entry, "Desktop pet closed unexpectedly. Using in-app display; retry desktop.")
  })
  void refreshOverlayCapabilities()
  watch(
    () => [
      extState.hostKey,
      [...overlays.values()]
        .map((o) => `${o.key}|${desired(o)}|${isOverlayVisible(o.key)}|${Boolean(o.contentReady)}`)
        .join(","),
    ],
    syncOverlayWindows,
    { immediate: true },
  )
  window.addEventListener("storage", (event) => {
    if (isPlacementStorageKey(event.key)) reloadPlacements()
  })
}
