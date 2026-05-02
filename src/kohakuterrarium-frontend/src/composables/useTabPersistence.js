/**
 * Persist the macro shell's tab list + active id in localStorage.
 *
 * Replaces the earlier URL-query approach so the address bar stays
 * clean. localStorage is per-origin per-browser-profile, which matches
 * the user's mental model for "where were my tabs last time" better
 * than a URL — bookmarking the macro shell's tab list was never a
 * supported flow anyway.
 *
 * Listens for the ``kt:tabs:dirty`` event the store dispatches on
 * every CRUD action (debounced 50 ms to coalesce bursts during reorder
 * + open + activate sequences).
 */

import { onBeforeUnmount, onMounted } from "vue"

import { useTabsStore } from "@/stores/tabs"

const STORAGE_KEY = "kt.tabs.state"
const DEBOUNCE_MS = 50

export function useTabPersistence() {
  const tabs = useTabsStore()
  let timer = null

  function flushToStorage() {
    try {
      const snapshot = tabs.serializeToStorage()
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
    } catch {
      /* swallow — quota / privacy mode */
    }
  }

  function onDirty() {
    clearTimeout(timer)
    timer = setTimeout(flushToStorage, DEBOUNCE_MS)
  }

  function loadFromStorage() {
    let snapshot = null
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      snapshot = raw ? JSON.parse(raw) : null
    } catch {
      snapshot = null
    }
    tabs.loadFromStorage(snapshot)
    // Default Dashboard tab when the user has never opened anything,
    // so the shell never starts in a totally-empty state. Doing this
    // here rather than the host component guarantees ordering — the
    // load-from-storage has finished before we add the default.
    if (tabs.tabs.length === 0) {
      tabs.openTab({ kind: "dashboard", id: "dashboard" })
    }
  }

  onMounted(() => {
    if (typeof window !== "undefined") {
      window.addEventListener("kt:tabs:dirty", onDirty)
    }
    loadFromStorage()
  })

  onBeforeUnmount(() => {
    if (typeof window !== "undefined") {
      window.removeEventListener("kt:tabs:dirty", onDirty)
    }
    clearTimeout(timer)
  })
}
