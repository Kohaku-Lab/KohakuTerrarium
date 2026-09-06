import { reactive } from "vue"

import { ensureUIPrefsLoaded, getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"

export const ATTENTION_PREF_KEYS = {
  dynamicTitle: "kt.attention.dynamicTitle",
  completionBadge: "kt.attention.completionBadge",
  inputRequiredBadge: "kt.attention.inputRequiredBadge",
  systemNotifications: "kt.attention.systemNotifications",
  notifyWaiting: "kt.attention.notifyWaiting",
  notifyCompletion: "kt.attention.notifyCompletion",
  notificationPreview: "kt.attention.notificationPreview",
  attentionSound: "kt.attention.attentionSound",
  soundWaiting: "kt.attention.soundWaiting",
  soundCompletion: "kt.attention.soundCompletion",
  faviconBadge: "kt.attention.faviconBadge",
  desktopAttention: "kt.attention.desktopAttention",
}

export const attentionPrefDefaults = {
  dynamicTitle: true,
  completionBadge: true,
  inputRequiredBadge: true,
  systemNotifications: false,
  notifyWaiting: true,
  notifyCompletion: false,
  notificationPreview: true,
  attentionSound: false,
  soundWaiting: true,
  soundCompletion: false,
  faviconBadge: true,
  desktopAttention: true,
}

const state = reactive({ ...attentionPrefDefaults })

function hydrateAttentionPrefs() {
  for (const [name, key] of Object.entries(ATTENTION_PREF_KEYS)) {
    state[name] = !!getHybridPrefSync(key, attentionPrefDefaults[name], { json: true })
  }
}

export function initializeAttentionPrefs() {
  hydrateAttentionPrefs()
  void ensureUIPrefsLoaded().then(hydrateAttentionPrefs)
}

export function useAttentionPrefs() {
  function set(name, value) {
    if (!(name in ATTENTION_PREF_KEYS)) return
    state[name] = !!value
    setHybridPref(ATTENTION_PREF_KEYS[name], state[name], { json: true })
  }

  return { state, set }
}
