import { readonly, ref } from "vue"

import { getShellSettings, patchShellSettings } from "@/utils/hostBridge"

const settings = ref(null)
let loading = null

/** Read the shell settings once and share them; resolves ``null`` outside the Tauri shell. */
export function refreshShellSettings() {
  loading ??= getShellSettings()
    .then((value) => {
      settings.value = value
      return value
    })
    .finally(() => {
      loading = null
    })
  return loading
}

/** Patch shell settings. Resolves ``true`` once the shell accepted and saved them. */
export async function patchShellSetting(patch) {
  const next = await patchShellSettings(patch)
  if (!next) return false
  settings.value = next
  return true
}

/** Forget what was read; the next refresh asks the shell again. */
export function resetShellSettings() {
  settings.value = null
  loading = null
}

/** Shared view of the shell settings: `{ settings, refresh, patch }`. */
export function useShellSettings() {
  if (settings.value === null) void refreshShellSettings()
  return { settings: readonly(settings), refresh: refreshShellSettings, patch: patchShellSetting }
}
