// Host locale seam. The webview owns no locale table: it reads the locale the
// host already resolved (``<html lang>`` is populated from ``vscode.env.language``)
// and lets the shared locale store normalise it against the real dictionaries.
const FALLBACK_LOCALE = 'en'
export const LOCALE_PREF_KEY = 'kt-locale'

export function hostLocale(doc = globalThis.document) {
  const declared = doc?.documentElement?.getAttribute?.('lang') || ''
  return declared.trim() || FALLBACK_LOCALE
}

// The locale is the one preference the host owns; every other pref resolves to
// its fallback because the extension persists nothing back into the webview.
// There is intentionally no ``setHostPref``: the webview cannot write the host
// language, so a write must fail loudly rather than pretend it was accepted.
export function getHostPref(key, fallback = null) {
  return key === LOCALE_PREF_KEY ? hostLocale() : fallback
}

export function rejectHostPrefWrite(key) {
  throw new Error(`KohakuTerrarium webview is read-only for host preferences (cannot persist "${key}")`)
}
