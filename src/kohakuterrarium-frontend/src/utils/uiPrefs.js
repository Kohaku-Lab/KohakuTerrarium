import { settingsAPI } from "@/utils/api"

const backendCache = new Map()
let loadPromise = null
let saveTimer = null
const pending = new Map()

function hasLocalStorage() {
  return typeof localStorage !== "undefined"
}

export function readLocalPref(key) {
  if (!hasLocalStorage()) return null
  const value = localStorage.getItem(key)
  return value == null ? null : value
}

export function writeLocalPref(key, value) {
  if (!hasLocalStorage()) return
  if (value == null) localStorage.removeItem(key)
  else localStorage.setItem(key, String(value))
}

export function readLocalJsonPref(key, fallback) {
  const raw = readLocalPref(key)
  if (raw == null) return fallback
  try {
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

export function writeLocalJsonPref(key, value) {
  if (value == null) writeLocalPref(key, null)
  else writeLocalPref(key, JSON.stringify(value))
}

function scheduleBackendFlush() {
  if (saveTimer != null) return
  saveTimer = setTimeout(async () => {
    saveTimer = null
    if (pending.size === 0) return
    const values = Object.fromEntries(pending)
    pending.clear()
    try {
      const data = await settingsAPI.updateUIPrefs(values)
      const merged = data?.values || {}
      for (const [key, value] of Object.entries(merged)) backendCache.set(key, value)
    } catch {
      for (const [key, value] of Object.entries(values)) pending.set(key, value)
    }
  }, 50)
}

export async function ensureUIPrefsLoaded() {
  if (!loadPromise) {
    loadPromise = settingsAPI
      .getUIPrefs()
      .then((data) => {
        const values = data?.values || {}
        for (const [key, value] of Object.entries(values)) {
          backendCache.set(key, value)
        }
        return values
      })
      .catch(() => ({}))
  }
  return loadPromise
}

export function getHybridPrefSync(key, fallback = null, opts = {}) {
  const { json = false } = opts
  const localValue = json ? readLocalJsonPref(key, null) : readLocalPref(key)
  if (localValue != null) return localValue
  if (!backendCache.has(key)) return fallback
  const backendValue = backendCache.get(key)
  if (json) writeLocalJsonPref(key, backendValue)
  else writeLocalPref(key, backendValue)
  return backendValue
}

export async function getHybridPref(key, fallback = null, opts = {}) {
  const localOrCached = getHybridPrefSync(key, null, opts)
  if (localOrCached != null) return localOrCached
  await ensureUIPrefsLoaded()
  return getHybridPrefSync(key, fallback, opts)
}

export function setHybridPref(key, value, opts = {}) {
  const { json = false } = opts
  if (json) writeLocalJsonPref(key, value)
  else writeLocalPref(key, value)
  backendCache.set(key, value)
  pending.set(key, value)
  scheduleBackendFlush()
}

export function removeHybridPref(key) {
  writeLocalPref(key, null)
  backendCache.delete(key)
  pending.set(key, null)
  scheduleBackendFlush()
}

export function _resetUIPrefsForTests() {
  backendCache.clear()
  pending.clear()
  loadPromise = null
  if (saveTimer != null) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
}
