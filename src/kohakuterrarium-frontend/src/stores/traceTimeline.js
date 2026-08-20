/**
 * Trace-timeline store — compact per-event spans for one (session, agent).
 *
 * Backs the lane overview above the trace turn list. Unlike the
 * per-turn event stores this loads the whole (bounded) span sequence at
 * once from ``GET /sessions/{n}/timeline``; live-attach events are
 * appended incrementally so the overview tracks a running session.
 *
 * **Per-scope** (scope = session name).
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { normalizeSpan } from "@/components/sessions/trace/traceTimeline"
import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { sessionAPI } from "@/utils/api"

const _traceTimelineOptions = {
  state: () => ({
    sessionName: "",
    agent: "",
    records: [],
    truncated: false,
    loading: false,
    loadGeneration: 0,
    loadingGeneration: null,
    error: "",
  }),

  actions: {
    async load(sessionName, agent = null) {
      if (!sessionName) return
      this.loadGeneration += 1
      const generation = this.loadGeneration
      const isSwitch = sessionName !== this.sessionName || (agent || "") !== this.agent
      this.sessionName = sessionName
      this.agent = agent || ""
      if (isSwitch) {
        this.records = []
        this.truncated = false
        this.error = ""
      }
      this.loading = true
      this.loadingGeneration = generation
      try {
        const data = await sessionAPI.getTimeline(sessionName, { agent })
        if (generation !== this.loadGeneration) return
        this.records = (data.spans || []).map(normalizeSpan).filter(Boolean)
        this.truncated = Boolean(data.truncated)
        this.agent = data.agent || agent || ""
      } catch (err) {
        if (generation !== this.loadGeneration) return
        this.error = `Failed to load timeline: ${err.message || err}`
        this.records = []
        this.truncated = false
      } finally {
        if (this.loadingGeneration === generation) {
          this.loading = false
          this.loadingGeneration = null
        }
      }
    },

    /** Append one live-attach event; dedupe on event id. */
    appendLive(eventObj) {
      const rec = normalizeSpan(eventObj)
      if (!rec) return
      if (rec.eid !== null && this.records.some((r) => r.eid === rec.eid)) return
      this.records.push(rec)
    },

    clear() {
      this.loadGeneration += 1
      this.sessionName = ""
      this.agent = ""
      this.records = []
      this.truncated = false
      this.loading = false
      this.loadingGeneration = null
      this.error = ""
    },
  },
}

const _traceTimelineFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _traceTimelineFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`traceTimeline:${key}`, _traceTimelineOptions)
    _traceTimelineFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _traceTimelineFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useTraceTimelineStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
