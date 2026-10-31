/**
 * Which settings section is open. Shared so that anything in the app can open the settings tab
 * on a given section; the last choice is remembered on this device.
 */

import { ref } from "vue"

import { useTabsStore } from "@/stores/tabs"

const STORAGE_KEY = "kt.settings.section"
const DEFAULT_SECTION = "providers"
const MAX_FRAMES = 40

function remembered() {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_SECTION
  } catch {
    return DEFAULT_SECTION
  }
}

export const activeSection = ref(remembered())

let directOpen = false

/** Whether the section was opened on purpose from elsewhere (read once; a narrow screen then skips the list). */
export function consumeDirectOpen() {
  const value = directOpen
  directOpen = false
  return value
}

export function selectSection(id) {
  if (!id) return
  activeSection.value = id
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    /* storage unavailable: the choice only lasts for this session */
  }
}

function revealAnchor(anchor) {
  let frames = 0
  const reveal = () => {
    const target = document.getElementById(anchor)
    if (target) {
      target.scrollIntoView({ block: "start" })
      return
    }
    frames += 1
    if (frames < MAX_FRAMES) requestAnimationFrame(reveal)
  }
  requestAnimationFrame(reveal)
}

/** Open the settings tab on ``id``, scrolling to the element ``anchor`` once it has rendered. */
export function openSettingsSection(id, { anchor = "" } = {}) {
  const tabs = useTabsStore()
  tabs.openTab({ kind: "settings", id: "settings" })
  tabs.activateTab("settings")
  directOpen = true
  selectSection(id)
  if (anchor) revealAnchor(anchor)
}
