/**
 * Turn-rollup store — drives the trace timeline + collapsed turn list.
 *
 * Cached per ``(sessionName, agent)`` — switching agents within the
 * same session re-fetches; switching sessions clears.
 *
 * Refresh policy: lazy. The store does not subscribe to live events —
 * the caller (TraceTab) decides when to invalidate, e.g. after a
 * live-attach burst settles.
 *
 * **Per-scope** (scope = session name).
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { sessionAPI } from "@/utils/api"

const _turnRollupOptions = {
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
}

const _turnRollupFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _turnRollupFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`turnRollup:${key}`, _turnRollupOptions)
    _turnRollupFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _turnRollupFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useTurnRollupStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
