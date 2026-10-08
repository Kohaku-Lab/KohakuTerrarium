/**
 * Which app the shell is in: "terrarium" (the lab and its sessions) or
 * "studio" (authoring). The home tab shows the lab or Studio accordingly.
 * One module-level cell, remembered per browser.
 */

import { ref } from "vue"

export const APP_MODES = ["terrarium", "studio"]
const KEY = "kt.appMode"

function read() {
  try {
    const raw = localStorage.getItem(KEY)
    return APP_MODES.includes(raw) ? raw : "terrarium"
  } catch {
    return "terrarium"
  }
}

const mode = ref(read())

export function setAppMode(next) {
  if (!APP_MODES.includes(next)) return
  mode.value = next
  try {
    localStorage.setItem(KEY, next)
  } catch {
    /* the mode just isn't remembered */
  }
}

export function useAppMode() {
  return { appMode: mode, setAppMode }
}

export function _resetAppModeForTests() {
  mode.value = read()
}
