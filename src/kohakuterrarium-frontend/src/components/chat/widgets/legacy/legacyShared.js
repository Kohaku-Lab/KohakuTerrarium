/**
 * Helpers shared by the built-in legacy renderers (card, ask_text, confirm,
 * selection, progress, notification): resolution state and style maps.
 */

import { computed } from "vue"

import "./legacy.css"

/** Reactive resolution flags of a legacy widget (answered / elsewhere / timed out). */
export function useResolution(props) {
  const resolution = computed(() => props.widget.resolution || {})
  const isResolved = computed(
    () =>
      resolution.value.replied === true ||
      resolution.value.superseded === true ||
      resolution.value.timedOut === true,
  )
  const resolvedLabel = computed(() => {
    if (resolution.value.timedOut) return "timed out"
    if (resolution.value.superseded) return "answered elsewhere"
    if (resolution.value.replied) return "answered"
    return ""
  })
  const repliedActionId = computed(() => resolution.value.repliedActionId || "")
  const repliedValues = computed(() => resolution.value.repliedValues || null)
  return { isResolved, resolvedLabel, repliedActionId, repliedValues }
}

export function confirmButtonType(style) {
  if (style === "primary") return "primary"
  if (style === "danger") return "danger"
  return ""
}

export function notificationIconClass(level) {
  return (
    {
      info: "i-carbon-information text-iolite",
      success: "i-carbon-checkmark-filled text-aquamarine",
      warning: "i-carbon-warning-alt text-amber",
      error: "i-carbon-error-filled text-coral",
    }[level || "info"] || "i-carbon-information text-iolite"
  )
}

export function progressPercent(spec) {
  const v = spec?.value
  const m = spec?.max
  if (v == null || !m) return null
  return Math.max(0, Math.min(100, Math.round((v * 100) / m)))
}

/** Minimized-chrome icon and one-line summary for each legacy renderer id. */
export const LEGACY_CHROME = {
  "kt.ask_text": {
    icon: () => "i-carbon-chat text-iolite",
    summary: (w) => w.spec?.prompt || "Input requested",
  },
  "kt.confirm": {
    icon: () => "i-carbon-warning-alt text-amber",
    summary: (w) => w.spec?.prompt || "Confirm",
  },
  "kt.selection": {
    icon: () => "i-carbon-list-checked text-iolite",
    summary: (w) => w.spec?.prompt || "Selection",
  },
  "kt.progress": {
    icon: (w) =>
      w.spec?.complete ? "i-carbon-checkmark text-aquamarine" : "i-carbon-time text-iolite",
    summary: (w) => w.spec?.label || "Progress",
  },
  "kt.notification": {
    icon: (w) => notificationIconClass(w.spec?.level),
    summary: (w) => w.spec?.text || "",
  },
  "kt.card": {
    icon: () => "i-carbon-document text-warm-500",
    summary: (w) => w.spec?.title || "Card",
  },
}
