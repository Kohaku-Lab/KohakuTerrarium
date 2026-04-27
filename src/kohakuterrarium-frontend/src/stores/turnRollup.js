/**
 * Turn-rollup store — drives the trace timeline + collapsed turn list.
 *
 * Cached per ``(sessionName, agent)`` — switching agents within the
 * same session re-fetches; switching sessions clears.
 *
 * Refresh policy: lazy. The store does not subscribe to live events —
 * the caller (TraceTab) decides when to invalidate, e.g. after a
 * live-attach burst settles.
 */

import { defineStore } from "pinia"

import { sessionAPI } from "@/utils/api"

export const useTurnRollupStore = defineStore("turnRollup", {
  state: () => ({
    sessionName: "",
    agent: "",
    aggregate: false,
    turns: [],
    total: 0,
    loading: false,
    error: "",
  }),

  getters: {
    /** Highest cost across the loaded turns — used for heatmap normalisation. */
    maxCost(state) {
      let max = 0
      for (const t of state.turns) {
        const c = Number(t.cost_usd || 0)
        if (c > max) max = c
      }
      return max
    },

    /** Token volume per turn, for the cost-fallback heatmap. */
    maxTokenVolume(state) {
      let max = 0
      for (const t of state.turns) {
        const v = Number(t.tokens_in || 0) + Number(t.tokens_out || 0)
        if (v > max) max = v
      }
      return max
    },

    /** ``true`` when no row has a non-null cost — caller falls back to tokens. */
    costAvailable: (state) => state.turns.some((t) => t.cost_usd != null),
  },

  actions: {
    async load(sessionName, agent = null, { aggregate = false } = {}) {
      if (!sessionName) return
      const isSwitch =
        sessionName !== this.sessionName ||
        (agent || "") !== this.agent ||
        aggregate !== this.aggregate
      this.sessionName = sessionName
      this.agent = agent || ""
      this.aggregate = aggregate
      if (isSwitch) {
        this.turns = []
        this.total = 0
        this.error = ""
      }
      this.loading = true
      try {
        const data = await sessionAPI.getTurns(sessionName, {
          agent,
          limit: 1000,
          aggregate,
        })
        this.turns = data.turns || []
        this.total = data.total || 0
        this.agent = data.agent || agent || ""
      } catch (err) {
        this.error = `Failed to load turns: ${err.message || err}`
        this.turns = []
        this.total = 0
      } finally {
        this.loading = false
      }
    },

    clear() {
      this.sessionName = ""
      this.agent = ""
      this.turns = []
      this.total = 0
      this.error = ""
    },
  },
})
