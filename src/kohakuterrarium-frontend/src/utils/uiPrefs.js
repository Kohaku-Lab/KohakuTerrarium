import { settingsAPI } from "@/utils/api"

// ── Hybrid UI-preference storage ─────────────────────────────────────
//
// localStorage is ALWAYS the primary store: reads are synchronous and
// writes land locally first, so no user-visible path ever waits on the
// network. The backend copy (`/settings/ui-prefs`) exists only so real
// UI *settings* (theme, zoom, layout, locale…) survive a fresh browser
// profile. It is written fire-and-forget, coalesced, and debounced.
//
// High-frequency ephemeral state (chat input drafts, per-instance tab
// state) must NOT go through the hybrid setters — use the local-only
// helpers below. One keystroke must never become one HTTP request.

const backendCache = new Map()
let loadPromise = null
let loadSettled = false
let saveTimer = null
const pending = new Map()
let backendWriteDisabled = false
let flushInFlight = false
let firstPendingAt = 0
let flushFailures = 0

// Trailing debounce: flush after the writes go quiet…
const FLUSH_IDLE_MS = 1500
// …but never sit on unflushed writes longer than this, even if the
// user keeps dragging a splitter / changing settings continuously.
const FLUSH_MAX_WAIT_MS = 10000
// Back-off before re-trying a transiently failed flush.
const FLUSH_RETRY_MS = 10000
// After this many consecutive failures, stop auto-retrying — the next
// user-initiated write re-arms the flush. Prevents polling a dead
// backend forever from an idle tab.
const FLUSH_MAX_RETRIES = 5

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

function prefValuesEqual(a, b) {
  if (a === b) return true
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

function scheduleBackendFlush(delay = FLUSH_IDLE_MS) {
  if (backendWriteDisabled || pending.size === 0) return
  const now = Date.now()
  if (!firstPendingAt) firstPendingAt = now
  // Debounce resets on every write, but the max-wait deadline is
  // anchored to the FIRST unflushed write so a continuous stream of
  // changes still checkpoints periodically.
  const deadline = firstPendingAt + FLUSH_MAX_WAIT_MS
  const wait = Math.max(0, Math.min(delay, deadline - now))
  if (saveTimer != null) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    void flushPendingNow()
  }, wait)
}

async function flushPendingNow() {
  if (backendWriteDisabled || flushInFlight || pending.size === 0) return
  flushInFlight = true
  if (saveTimer != null) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  const values = Object.fromEntries(pending)
  pending.clear()
  firstPendingAt = 0
  try {
    const data = await settingsAPI.updateUIPrefs(values)
    const merged = data?.values || {}
    for (const [key, value] of Object.entries(merged)) backendCache.set(key, value)
    // Keys we asked the backend to delete don't come back in the
    // merged view — drop them from the cache too.
    for (const [key, value] of Object.entries(values)) {
      if (value == null && !(key in merged)) backendCache.delete(key)
    }
    flushFailures = 0
  } catch (error) {
    const status = error?.response?.status
    if (status === 404 || status === 405 || status === 501) {
      backendWriteDisabled = true
      flushInFlight = false
      return
    }
    // Re-queue, but never clobber a newer value written while the
    // failed request was in flight.
    for (const [key, value] of Object.entries(values)) {
      if (!pending.has(key)) pending.set(key, value)
    }
    flushInFlight = false
    flushFailures += 1
    if (flushFailures < FLUSH_MAX_RETRIES) scheduleBackendFlush(FLUSH_RETRY_MS)
    return
  }
  flushInFlight = false
  // Writes that arrived while the request was in flight were parked
  // in `pending` (flushPendingNow no-ops when in flight) — pick them
  // up now instead of waiting for the next user action.
  if (pending.size > 0) scheduleBackendFlush()
}

function flushOnHide() {
  // Best-effort checkpoint when the tab goes away; localStorage
  // already holds the truth, so a lost request only delays the
  // cross-device copy.
  if (pending.size === 0) return
  void flushPendingNow()
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("pagehide", flushOnHide)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushOnHide()
  })
}

export async function ensureUIPrefsLoaded(opts = {}) {
  const { timeoutMs = 0 } = opts
  if (!loadPromise) {
    loadPromise = settingsAPI
      .getUIPrefs()
      .then((data) => {
        const values = data?.values || {}
        for (const [key, value] of Object.entries(values)) {
          backendCache.set(key, value)
        }
        loadSettled = true
        return values
      })
      .catch(() => {
        loadSettled = true
        return {}
      })
  }
  if (timeoutMs > 0) {
    // Boot paths must not hang on a slow backend — the promise keeps
    // filling the cache in the background after the race resolves.
    return Promise.race([
      loadPromise,
      new Promise((resolve) => setTimeout(() => resolve({}), timeoutMs)),
    ])
  }
  return loadPromise
}

export function getHybridPrefSync(key, fallback = null, opts = {}) {
  const { json = false } = opts
  const localValue = json ? readLocalJsonPref(key, null) : readLocalPref(key)
  if (localValue != null) return localValue
  if (!backendCache.has(key)) return fallback
  const backendValue = backendCache.get(key)
  if (backendValue == null) return fallback
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
  if (backendWriteDisabled) {
    // Keep the in-memory cache coherent — it's the read fallback
    // when localStorage is unavailable.
    backendCache.set(key, value)
    return
  }
  // Skip no-op writes: stores re-apply unchanged values on init /
  // route changes, and each used to become its own POST.
  if (backendCache.has(key) && prefValuesEqual(backendCache.get(key), value)) return
  backendCache.set(key, value)
  pending.set(key, value)
  // A fresh user write re-arms a flush loop that gave up after
  // repeated failures.
  flushFailures = 0
  scheduleBackendFlush()
}

export function removeHybridPref(key) {
  writeLocalPref(key, null)
  if (backendWriteDisabled) {
    backendCache.delete(key)
    return
  }
  // Once the initial load settled we know what the backend holds —
  // removing a key it never had needs no request.
  const knownAbsent = loadSettled && !backendCache.has(key) && !pending.has(key)
  backendCache.delete(key)
  if (knownAbsent) return
  pending.set(key, null)
  flushFailures = 0
  scheduleBackendFlush()
}

export function _resetUIPrefsForTests() {
  backendCache.clear()
  pending.clear()
  loadPromise = null
  loadSettled = false
  backendWriteDisabled = false
  flushInFlight = false
  firstPendingAt = 0
  flushFailures = 0
  if (saveTimer != null) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
}
