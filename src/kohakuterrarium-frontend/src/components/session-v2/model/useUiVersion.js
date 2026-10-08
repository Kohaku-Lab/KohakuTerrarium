/**
 * useUiVersion — which session shell the app uses: "v1" (the panel
 * workbench: split tree, presets, layout editing) or "v2" (fixed session
 * sections with a centered chat and a floating dock). Persisted to the
 * hybrid pref `kt-session-shell`; a module-level cell shared by every
 * consumer, synced across browser tabs, and refreshed once the backend
 * prefs arrive unless the user picked a shell first.
 */

import { computed, ref } from "vue"

import { ensureUIPrefsLoaded, getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const UI_VERSIONS = ["v1", "v2"]
export const DEFAULT_UI_VERSION = "v1"
export const UI_VERSION_KEY = "kt-session-shell"

const hasWindow = typeof window !== "undefined"
const _version = ref(DEFAULT_UI_VERSION)
let _initialized = false
let _picked = false

function readStored() {
  const raw = getHybridPrefSync(UI_VERSION_KEY, DEFAULT_UI_VERSION)
  return UI_VERSIONS.includes(raw) ? raw : DEFAULT_UI_VERSION
}

function _initialize() {
  if (_initialized) return
  _initialized = true
  _version.value = readStored()
  ensureUIPrefsLoaded()
    .then(() => {
      if (!_picked) _version.value = readStored()
    })
    .catch(() => {})
  if (hasWindow)
    window.addEventListener("storage", (e) => {
      if (e.key === UI_VERSION_KEY) _version.value = readStored()
    })
}

function setUiVersion(value) {
  if (!UI_VERSIONS.includes(value)) return
  _initialize()
  _picked = true
  _version.value = value
  setHybridPref(UI_VERSION_KEY, value)
}

export function useUiVersion() {
  _initialize()
  return {
    uiVersion: _version,
    isV2: computed(() => _version.value === "v2"),
    setUiVersion,
  }
}

export function _resetUiVersionForTests() {
  _initialized = false
  _picked = false
  _version.value = DEFAULT_UI_VERSION
}
