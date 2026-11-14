import {
  overlays,
  placements,
  isOverlayVisible,
  showOverlay,
  restoreVisibility,
  petSelection,
  petSelectionKey,
  restorePetSelection,
} from "./overlays"
import { getHybridPref } from "@/utils/uiPrefs"

const checked = new Set()
const defaults = new Set()
export const visibilityKey = (hostKey) => `kt.overlayVisibility.${hostKey}`

export async function restoreStartupVisibility(hostKey, isCurrent) {
  let saved, selected
  try {
    ;[saved, selected] = await Promise.all([
      getHybridPref(visibilityKey(hostKey), {}, { json: true }),
      getHybridPref(petSelectionKey(hostKey), null, { json: true }),
    ])
  } catch {
    return
  }
  if (!isCurrent() || !saved || typeof saved !== "object" || Array.isArray(saved)) return
  if (selected) restorePetSelection(selected)
  for (const [key, visible] of Object.entries(saved)) {
    if (
      key.startsWith("ext:") &&
      typeof visible === "boolean" &&
      typeof placements[key]?.visible !== "boolean"
    )
      restoreVisibility(key, visible)
  }
}

export function applyStartupFallbacks(hostKey) {
  if (checked.has(hostKey)) return
  checked.add(hostKey)
  const groups = new Set([...overlays.values()].map((o) => o.options.startupGroup).filter(Boolean))
  for (const group of groups) {
    const members = [...overlays.values()].filter((o) => o.options.startupGroup === group)
    if (group === "pet") {
      const explicit =
        placements.$pet || members.some((o) => typeof placements[o.key]?.visible === "boolean")
      restorePetSelection()
      const choice = petSelection()
      if (!explicit && choice.visible && overlays.get(choice.selected)?.options.startupFallback)
        defaults.add(`${hostKey}\u0000${choice.selected}`)
      continue
    }
    if (members.some((o) => isOverlayVisible(o.key))) continue
    const fallback = members.find((o) => o.options.startupFallback === true)
    if (!fallback) continue
    defaults.add(`${hostKey}\u0000${fallback.key}`)
    showOverlay(fallback.key)
  }
}

export function consumeStartupFallback(key, hostKey) {
  return defaults.delete(`${hostKey}\u0000${key}`)
}

export function resetStartupChecks() {
  checked.clear()
  defaults.clear()
}
