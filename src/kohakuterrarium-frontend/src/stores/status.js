/**
 * Status dashboard store.
 *
 * Tracks session metadata, token usage, running jobs, and scratchpad
 * state. Populated from WebSocket activity events forwarded by the
 * chat store.
 */

export const useStatusStore = defineStore("status", {
  state: () => ({
    sessionInfo: {
      agentName: "",
      sessionId: "",
      model: "",
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
  }),

  getters: {
    hasRunningJobs: (state) => state.runningJobs.length > 0,
    jobCount: (state) => state.runningJobs.length,
  },

  actions: {
    /** Handle a WS activity event. Call from chat store's _handleActivity. */
    handleActivity(data) {
      const at = data.activity_type

      if (at === "session_info") {
        this.sessionInfo = {
          agentName: data.agent_name || this.sessionInfo.agentName,
          sessionId: data.session_id || this.sessionInfo.sessionId,
          model: data.model || this.sessionInfo.model,
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
    },
  },
})
