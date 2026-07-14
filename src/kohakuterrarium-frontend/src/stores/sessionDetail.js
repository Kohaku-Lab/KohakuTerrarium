/**
 * Session-detail store — owns the active session being browsed in the
 * Session Viewer. Loads ``meta`` (via the existing history-index
 * endpoint), ``tree`` (V1) and ``summary`` (V1) on demand.
 *
 * Tab state lives here too so deep-linking via ``?tab=trace`` survives
 * a refresh and switching tabs doesn't refetch the tree / summary.
 *
 * **Per-scope** (scope = session name). Two macro-shell session-viewer
 * tabs for different saved sessions each get their own bucket and
 * don't trample each other's tab state / loaded meta. Outside a
 * provider (v1 page route) the default-scope store keeps the
 * historical singleton behaviour.
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { sessionAPI } from "@/utils/api"

const VALID_TABS = new Set(["overview", "trace", "conv", "cost", "find", "diff", "drives"])

const _sessionDetailOptions = {
  state: () => ({
    name: "",
    activeTab: "overview",
    meta: null,
    targets: [],
    tree: null,
    summary: null,
    loadingMeta: false,
    loadingTree: false,
    loadingSummary: false,
    error: "",
    // True when the bound session is LIVE (the inspector embeds the
    // viewer for a running graph): the Drives tab then reads the live
    // ``/sessions/{sid}/drives`` route, whose offline saved counterpart
    // returns [] under the live writer lock (UXI-01). Saved viewers keep
    // it false and read the persisted sidecar.
    live: false,
    // Bumped by ``requestReload`` to tell the per-tab loaders (Cost /
    // Trace / Conversation) to refetch their data as a LIVE session
    // progresses. Never bumped for a saved session (UXI-01 live viewer).
    reloadKey: 0,
  }),

  getters: {
    // Enumerate EVERY creature in the graph, not just the root. The
    // no-agent ``/summary`` lists all creatures (+ attached namespaces)
    // and the history-index ``targets`` are the exact source the
    // conversation dropdown uses; union both (summary first for the
    // viewer-default ordering) and fall back to ``meta.agents`` before
    // either has loaded (UXI-03). Channel targets (``ch:``) are excluded.
    agents: (state) => {
      const names = []
      const seen = new Set()
      const add = (n) => {
        if (typeof n === "string" && n && !n.startsWith("ch:") && !seen.has(n)) {
          seen.add(n)
          names.push(n)
        }
      }
      for (const n of (state.summary && state.summary.agents) || []) add(n)
      for (const n of state.targets || []) add(n)
      for (const n of (state.meta && state.meta.agents) || []) add(n)
      return names
    },
    primaryAgent() {
      return this.agents[0] || null
    },
    formatVersion: (state) => state.meta && state.meta.format_version,
    isMigrated: (state) => state.meta && state.meta.format_version === 1,
  },

  actions: {
    setTab(tab) {
      this.activeTab = VALID_TABS.has(tab) ? tab : "overview"
    },

    /**
     * Refresh the viewer's data in place as a live session advances:
     * refetch meta / tree / summary (Overview) and bump ``reloadKey`` so
     * the per-tab loaders (Cost / Trace / Conversation) refetch too.
     * ``load`` keeps the existing meta/summary while refetching (no
     * ``isSwitch``), so there is no spinner flash / scroll reset.
     */
    requestReload() {
      this.reloadKey++
      if (this.name) this.load(this.name)
    },

    async load(name) {
      if (!name) return
      const isSwitch = name !== this.name
      this.name = name
      if (isSwitch) {
        this.meta = null
        this.targets = []
        this.tree = null
        this.summary = null
        this.error = ""
      }
      await Promise.all([this.loadMeta(), this.loadTree(), this.loadSummary()])
    },

    async loadMeta() {
      if (!this.name) return
      this.loadingMeta = true
      try {
        const data = await sessionAPI.getHistoryIndex(this.name)
        this.meta = data.meta || null
        this.targets = data.targets || []
      } catch (err) {
        // A just-started / unpersisted live session has no saved index
        // yet — a 404 is a benign empty state, not a load failure.
        if (err?.response?.status === 404) {
          this.meta = null
          this.targets = []
        } else {
          this.error = `Failed to load session metadata: ${err.message || err}`
        }
      } finally {
        this.loadingMeta = false
      }
    },

    async loadTree() {
      if (!this.name) return
      this.loadingTree = true
      try {
        this.tree = await sessionAPI.getTree(this.name)
      } catch (err) {
        console.warn("Failed to load session tree:", err)
        this.tree = null
      } finally {
        this.loadingTree = false
      }
    },

    async loadSummary(agent = null) {
      if (!this.name) return
      this.loadingSummary = true
      try {
        this.summary = await sessionAPI.getSummary(this.name, agent)
      } catch (err) {
        console.warn("Failed to load session summary:", err)
        this.summary = null
      } finally {
        this.loadingSummary = false
      }
    },
  },
}

const _sessionDetailFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _sessionDetailFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`sessionDetail:${key}`, _sessionDetailOptions)
    _sessionDetailFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _sessionDetailFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useSessionDetailStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
