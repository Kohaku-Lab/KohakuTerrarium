import { getHostPref, rejectHostPrefWrite } from '../hostLocale.mjs'

export const translate = (_language, key) => key
export const readLocalJsonPref = (_key, fallback) => fallback
export const writeLocalJsonPref = () => {}
export const wsUrl = (route) => `bridge://${route}`

// Preference seam: the host language is the one pref the webview reads, resolved
// against the real dictionaries by the shared locale store. The webview owns no
// persistence, so a write is rejected explicitly instead of silently succeeding
// and being discarded on the next reload.
export const getHybridPrefSync = (key, fallback = null) => getHostPref(key, fallback)
export const setHybridPref = (key) => rejectHostPrefWrite(key)
