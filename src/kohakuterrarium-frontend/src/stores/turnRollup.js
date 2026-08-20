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

const TURN_PAGE_SIZE = 1000

function _scopeOf(store) {
  return {
    sessionName: store.sessionName,
    agent: store.agent,
    aggregate: store.aggregate,
  }
}

function _scopeIsActive(store, scope) {
  return (
    store.sessionName === scope.sessionName &&
    store.agent === scope.agent &&
    store.aggregate === scope.aggregate
  )
}

function _turnKey(turn) {
  return `${turn.member_sid || ""}\u0000${turn.agent || ""}\u0000${turn.turn_index}`
}

function _mergeTurns(...pages) {
  const turns = new Map()
  for (const page of pages) {
    for (const turn of page || []) turns.set(_turnKey(turn), turn)
  }
  return [...turns.values()].sort(
    (a, b) =>
      Number(a.turn_index || 0) - Number(b.turn_index || 0) ||
      String(a.agent || a.member_sid || "").localeCompare(String(b.agent || b.member_sid || "")),
  )
}

const _turnRollupOptions = {
  state: () => ({
    sessionName: "",
    agent: "",
    aggregate: false,
    turns: [],
    total: 0,
    windowOffset: 0,
    loading: false,
    loadingOlder: false,
    loadGeneration: 0,
    loadingGeneration: null,
    error: "",
    pageError: "",
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

    hasOlder: (state) => state.windowOffset > 0,
  },

  actions: {
    async load(sessionName, agent = null, { aggregate = false } = {}) {
      if (!sessionName) return
      this.loadGeneration += 1
      const generation = this.loadGeneration
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
        this.windowOffset = 0
        this.loadingOlder = false
      }
      this.error = ""
      this.pageError = ""
      this.loading = true
      this.loadingGeneration = generation
      try {
        const firstPage = await sessionAPI.getTurns(sessionName, {
          agent,
          limit: TURN_PAGE_SIZE,
          offset: 0,
          aggregate,
        })
        if (generation !== this.loadGeneration) return

        let data = firstPage
        const total = Number(firstPage.total || 0)
        const latestOffset = Math.max(0, total - TURN_PAGE_SIZE)
        const resolvedAgent = firstPage.agent || agent || ""
        if (latestOffset > 0) {
          data = await sessionAPI.getTurns(sessionName, {
            agent: resolvedAgent || null,
            limit: TURN_PAGE_SIZE,
            offset: latestOffset,
            aggregate,
          })
          if (generation !== this.loadGeneration) return
        }

        this.turns = data.turns || []
        this.total = Number(data.total ?? total)
        this.windowOffset = Number(data.offset ?? latestOffset)
        this.agent = data.agent || resolvedAgent
      } catch (err) {
        if (generation !== this.loadGeneration) return
        this.error = `Failed to load turns: ${err.message || err}`
        this.turns = []
        this.total = 0
        this.windowOffset = 0
      } finally {
        if (this.loadingGeneration === generation) {
          this.loading = false
          this.loadingGeneration = null
        }
      }
    },

    async loadOlder() {
      if (!this.sessionName || !this.hasOlder || this.loadingOlder) return false
      const scope = _scopeOf(this)
      const generation = this.loadGeneration
      const offset = Math.max(0, this.windowOffset - TURN_PAGE_SIZE)
      const limit = this.windowOffset - offset
      this.loadingOlder = true
      this.pageError = ""
      try {
        const data = await sessionAPI.getTurns(scope.sessionName, {
          agent: scope.agent || null,
          limit,
          offset,
          aggregate: scope.aggregate,
        })
        if (generation !== this.loadGeneration || !_scopeIsActive(this, scope)) return false
        this.turns = _mergeTurns(data.turns, this.turns)
        this.total = Number(data.total ?? this.total)
        this.windowOffset = Number(data.offset ?? offset)
        return true
      } catch (err) {
        if (generation === this.loadGeneration && _scopeIsActive(this, scope)) {
          this.pageError = `Failed to load earlier turns: ${err.message || err}`
        }
        return false
      } finally {
        if (_scopeIsActive(this, scope)) this.loadingOlder = false
      }
    },

    async ensureTurn(turnIndex) {
      const target = Number(turnIndex)
      if (!Number.isFinite(target) || !this.sessionName) return false
      if (this.turns.some((turn) => Number(turn.turn_index) === target)) return true

      const scope = _scopeOf(this)
      const generation = this.loadGeneration
      this.pageError = ""
      try {
        const data = await sessionAPI.getTurns(scope.sessionName, {
          agent: scope.agent || null,
          fromTurn: target,
          toTurn: target,
          limit: TURN_PAGE_SIZE,
          offset: 0,
          aggregate: scope.aggregate,
        })
        if (generation !== this.loadGeneration || !_scopeIsActive(this, scope)) return false
        const exact = (data.turns || []).filter((turn) => Number(turn.turn_index) === target)
        if (!exact.length) return false
        this.turns = _mergeTurns(this.turns, exact)
        return true
      } catch (err) {
        if (generation === this.loadGeneration && _scopeIsActive(this, scope)) {
          this.pageError = `Failed to load turn ${target}: ${err.message || err}`
        }
        return false
      }
    },

    clear() {
      this.loadGeneration += 1
      this.sessionName = ""
      this.agent = ""
      this.aggregate = false
      this.turns = []
      this.total = 0
      this.windowOffset = 0
      this.loading = false
      this.loadingOlder = false
      this.loadingGeneration = null
      this.error = ""
      this.pageError = ""
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
