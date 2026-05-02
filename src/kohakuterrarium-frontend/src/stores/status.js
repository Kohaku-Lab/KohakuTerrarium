/**
 * Status dashboard store.
 *
 * Tracks session metadata, token usage, running jobs, and scratchpad
 * state. Populated from WebSocket activity events forwarded by the
 * chat store.
 *
 * Per-attach scope: each running creature has its own running-jobs
 * list, token usage, scratchpad, etc. Two attach tabs for two
 * creatures of the same config must NOT share status state.
 * AttachTab provides ``kt:scope`` once at the top of its setup;
 * descendants that call :func:`useStatusStore` automatically pick
 * the per-attach store via inject. v1 page-routed flow falls
 * through to the default singleton.
 */

import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"

const _options = {
  state: () => ({
    sessionInfo: {
      agentName: "",
      sessionId: "",
      model: "",
      // Canonical ``provider/name[@variations]`` identifier — mirrors
      // the chat store so Dashboard/Status surfaces can display the
      // full form without reaching into the chat namespace.
      llmName: "",
      startTime: null,
    },
    tokenUsage: {
      promptTokens: 0,
      completionTokens: 0,
      cachedTokens: 0,
      contextPercent: 0,
      compactThreshold: 0,
    },
    /** @type {{ jobId: string, name: string, type: string, startTime: number, elapsed: number }[]} */
    runningJobs: [],
    /** @type {Object<string, string>} */
    scratchpad: {},
    /** @type {number | null} */
    _elapsedTimer: null,
    /**
     * Ring buffer of recent activity events for the Inspector Trace
     * pane. Last 100. Populated by ``handleActivity`` for any event
     * type. Consumers should treat the array as opaque and not mutate.
     * @type {Array<{ts: number, type: string, name?: string, detail?: string, data: object}>}
     */
    recentEvents: [],
  }),

  getters: {
    hasRunningJobs: (state) => state.runningJobs.length > 0,
    jobCount: (state) => state.runningJobs.length,
  },

  actions: {
    /** Handle a WS activity event. Call from chat store's _handleActivity. */
    handleActivity(data) {
      const at = data.activity_type
      // Push to ring buffer for the Inspector Trace pane.
      this.recentEvents.push({
        ts: Date.now(),
        type: at,
        name: data.name || data.tool_name || "",
        detail: data.detail || data.message || "",
        data,
      })
      if (this.recentEvents.length > 100) {
        this.recentEvents = this.recentEvents.slice(-100)
      }

      if (at === "session_info") {
        this.sessionInfo = {
          agentName: data.agent_name || this.sessionInfo.agentName,
          sessionId: data.session_id || this.sessionInfo.sessionId,
          model: data.model || this.sessionInfo.model,
          llmName: data.llm_name || this.sessionInfo.llmName,
          startTime: data.start_time
            ? new Date(data.start_time).getTime()
            : this.sessionInfo.startTime || Date.now(),
        }
        if (data.compact_threshold) {
          this.tokenUsage.compactThreshold = data.compact_threshold
        }
      } else if (at === "token_usage") {
        this.tokenUsage = {
          promptTokens: this.tokenUsage.promptTokens + (data.prompt_tokens || 0),
          completionTokens: this.tokenUsage.completionTokens + (data.completion_tokens || 0),
          cachedTokens: this.tokenUsage.cachedTokens + (data.cached_tokens || 0),
          contextPercent: data.context_percent ?? this.tokenUsage.contextPercent,
          compactThreshold: data.compact_threshold ?? this.tokenUsage.compactThreshold,
        }
      } else if (at === "tool_start") {
        const jobId = data.job_id || `job_${Date.now()}`
        this.runningJobs.push({
          jobId,
          name: data.name || "unknown",
          type: "tool",
          startTime: Date.now(),
          elapsed: 0,
        })
        this._ensureElapsedTimer()
      } else if (at === "subagent_start") {
        const jobId = data.job_id || `job_${Date.now()}`
        this.runningJobs.push({
          jobId,
          name: data.name || "unknown",
          type: "subagent",
          startTime: Date.now(),
          elapsed: 0,
        })
        this._ensureElapsedTimer()
      } else if (at === "tool_done" || at === "tool_error") {
        this._removeJob(data.name, data.job_id)
      } else if (at === "subagent_done" || at === "subagent_error") {
        this._removeJob(data.name, data.job_id)
      } else if (at === "scratchpad_update") {
        if (data.key) {
          if (data.value === null || data.value === undefined) {
            delete this.scratchpad[data.key]
          } else {
            this.scratchpad[data.key] = data.value
          }
        } else if (data.entries) {
          this.scratchpad = { ...data.entries }
        }
      }
    },

    /** Remove a job by name or id */
    _removeJob(name, id) {
      const idx = id
        ? this.runningJobs.findIndex((j) => j.jobId === id)
        : this.runningJobs.findIndex((j) => j.name === name)
      if (idx !== -1) {
        this.runningJobs.splice(idx, 1)
      }
      if (this.runningJobs.length === 0) {
        this._clearElapsedTimer()
      }
    },

    /** Start 1-second interval to update elapsed times */
    _ensureElapsedTimer() {
      if (this._elapsedTimer !== null) return
      this._elapsedTimer = setInterval(() => {
        const now = Date.now()
        for (const job of this.runningJobs) {
          job.elapsed = Math.floor((now - job.startTime) / 1000)
        }
      }, 1000)
    },

    /** Clear the elapsed timer */
    _clearElapsedTimer() {
      if (this._elapsedTimer !== null) {
        clearInterval(this._elapsedTimer)
        this._elapsedTimer = null
      }
    },

    /** Reset all status state (e.g. when switching instances) */
    reset() {
      this._clearElapsedTimer()
      this.sessionInfo = {
        agentName: "",
        sessionId: "",
        model: "",
        llmName: "",
        startTime: null,
      }
      this.tokenUsage = {
        promptTokens: 0,
        completionTokens: 0,
        cachedTokens: 0,
        contextPercent: 0,
        compactThreshold: 0,
      }
      this.runningJobs = []
      this.scratchpad = {}
      this.recentEvents = []
    },
  },
}

const _factories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _factories.get(key)
  if (!useFn) {
    useFn = defineStore(`status:${key}`, _options)
    _factories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn()._clearElapsedTimer?.()
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _factories.delete(key)
      })
    }
  }
  return useFn
}

export function useStatusStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
