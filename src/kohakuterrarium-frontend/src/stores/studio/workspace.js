import { defineStore } from "pinia"
import { computed, ref } from "vue"

import { workspaceAPI } from "@/utils/studio/api"

const RECENT_KEY = "studio:recent-workspaces"
const RECENT_MAX = 8

function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : []
  } catch {
    return []
  }
}

function saveRecent(list) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* noop */
  }
}

/**
 * Studio workspace store.
 *
 * Exactly one workspace is "open" at a time per process — the
 * backend enforces this. The store mirrors that assumption.
 */
export const useStudioWorkspaceStore = defineStore("studio-workspace", () => {
  const root = ref(null) // absolute path string
  const summary = ref(null) // { creatures: [], modules: { tools: [...], ... } }
  const loading = ref(false)
  const error = ref(null)
  const recent = ref(loadRecent())

  const isOpen = computed(() => root.value !== null)
  const creatures = computed(() => summary.value?.creatures ?? [])
  const modulesByKind = computed(() => summary.value?.modules ?? {})

  function pushRecent(path) {
    if (!path) return
    const filtered = recent.value.filter((p) => p !== path)
    filtered.unshift(path)
    recent.value = filtered.slice(0, RECENT_MAX)
    saveRecent(recent.value)
  }

  function clearRecent() {
    recent.value = []
    saveRecent(recent.value)
  }

  async function open(path) {
    loading.value = true
    error.value = null
    try {
      const res = await workspaceAPI.open(path)
      summary.value = res
      root.value = res.root
      pushRecent(res.root)
      return res
    } catch (err) {
      error.value = err
      throw err
    } finally {
      loading.value = false
    }
  }

  async function refresh() {
    if (!isOpen.value) return null
    loading.value = true
    try {
      const res = await workspaceAPI.get()
      summary.value = res
      root.value = res.root
      return res
    } catch (err) {
      error.value = err
      throw err
    } finally {
      loading.value = false
    }
  }

  async function close() {
    try {
      await workspaceAPI.close()
    } catch {
      /* backend may already be clear */
    }
    summary.value = null
    root.value = null
    error.value = null
  }

  /** Reload from backend if the store thinks it's open but maybe isn't. */
  async function hydrate() {
    loading.value = true
    try {
      const res = await workspaceAPI.get()
      summary.value = res
      root.value = res.root
    } catch (err) {
      // 409 means no workspace open on the server
      if (err?.status === 409) {
        summary.value = null
        root.value = null
      } else {
        error.value = err
      }
    } finally {
      loading.value = false
    }
  }

  return {
    // state
    root,
    summary,
    loading,
    error,
    recent,
    // getters
    isOpen,
    creatures,
    modulesByKind,
    // actions
    open,
    refresh,
    close,
    hydrate,
    pushRecent,
    clearRecent,
  }
})
