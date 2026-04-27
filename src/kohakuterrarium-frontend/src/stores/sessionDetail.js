/**
 * Session-detail store — owns the active session being browsed in the
 * Session Viewer. Loads ``meta`` (via the existing history-index
 * endpoint), ``tree`` (V1) and ``summary`` (V1) on demand.
 *
 * Tab state lives here too so deep-linking via ``?tab=trace`` survives
 * a refresh and switching tabs doesn't refetch the tree / summary.
 */

import { defineStore } from "pinia"

import { sessionAPI } from "@/utils/api"

const VALID_TABS = new Set(["overview", "trace", "conv", "cost", "find", "diff"])

export const useSessionDetailStore = defineStore("sessionDetail", {
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
  }),

  getters: {
    agents: (state) => (state.meta && state.meta.agents) || [],
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
      // Fetch metadata + tree + summary in parallel; surface the first
      // failure but keep partial loads visible.
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
        this.error = `Failed to load session metadata: ${err.message || err}`
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
        // Tree failure is non-fatal — the rest of the viewer still works.
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
})
