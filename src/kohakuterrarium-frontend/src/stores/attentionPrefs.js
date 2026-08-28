import { reactive } from "vue"

import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const ATTENTION_PREF_KEYS = {
  dynamicTitle: "kt.attention.dynamicTitle",
  completionBadge: "kt.attention.completionBadge",
  inputRequiredBadge: "kt.attention.inputRequiredBadge",
}

export const attentionPrefDefaults = {
  dynamicTitle: true,
  completionBadge: true,
  inputRequiredBadge: true,
}

const state = reactive(
  Object.fromEntries(
    Object.entries(ATTENTION_PREF_KEYS).map(([name, key]) => [
      name,
      getHybridPrefSync(key, attentionPrefDefaults[name], { json: true }),
    ]),
  ),
)

export function useAttentionPrefs() {
  function set(name, value) {
    if (!(name in ATTENTION_PREF_KEYS)) return
    state[name] = !!value
    setHybridPref(ATTENTION_PREF_KEYS[name], state[name], { json: true })
  }

  return { state, set }
}
