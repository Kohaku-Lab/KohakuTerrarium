import { defineStore } from "pinia"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"

const POLL_INTERVAL_MS = 5000
const SAME_ORIGIN_SCOPE = "_same_origin"

export const useConversationsStore = defineStore("conversations", {
  state: () => ({
    rows: [],
    loading: false,
    _pollInterval: null,
    _subscribers: 0,
    _inflightFetch: null,
    _queuedFetch: null,
    _fetchGeneration: 0,
    _scopeGeneration: 0,
    _hostScope: null,
    _unsubscribeHosts: null,
    _unsubscribeAuth: null,
    _resumePromises: {},
    _endPromises: {},
  }),

  getters: {
    isResuming: (state) => (id) => Boolean(state._resumePromises[`${state._hostScope}:${id}`]),
    liveRows: (state) => state.rows.filter((row) => row.is_live),
  },

  actions: {
    fetchAll({ force = false } = {}) {
      this._syncHostScope()
      if (this._queuedFetch) return this._queuedFetch
      if (this._inflightFetch) {
        return force ? this._queueFetch() : this._inflightFetch
      }
      return this._startFetch()
    },

    _startFetch() {
      const generation = this._fetchGeneration
      const hostScope = this._hostScope
      this.loading = true

      const task = (async () => {
        try {
          const conversations = await sessionAPI.listOpen()
          if (!this._ownsFetch(task, generation, hostScope)) return
          this.rows = conversations.map((row) => mapConversation(row, hostScope))
        } catch (err) {
          if (this._ownsFetch(task, generation, hostScope)) {
            console.error("Failed to fetch conversations:", err)
          }
        } finally {
          if (this._inflightFetch === task) {
            this._inflightFetch = null
            if (!this._queuedFetch) this.loading = false
          }
        }
      })()

      this._inflightFetch = task
      return task
    },

    _queueFetch() {
      const current = this._inflightFetch
      const generation = this._fetchGeneration
      const hostScope = this._hostScope
      let queued
      queued = (async () => {
        await current
        if (generation !== this._fetchGeneration || hostScope !== this._hostScope) return
        return this._startFetch()
      })().finally(() => {
        if (this._queuedFetch === queued) {
          this._queuedFetch = null
          if (!this._inflightFetch) this.loading = false
        }
      })
      this._queuedFetch = queued
      return queued
    },

    _ownsFetch(task, generation, hostScope) {
      return (
        this._inflightFetch === task &&
        generation === this._fetchGeneration &&
        hostScope === this._hostScope
      )
    },

    _syncHostScope() {
      const hostScope = getHostScope()
      if (this._hostScope === null) {
        this._hostScope = hostScope
      } else if (this._hostScope !== hostScope) {
        this._invalidateFetches({ clearRows: true })
        this._hostScope = hostScope
      }
      return hostScope
    },

    _invalidateListFetches() {
      this._fetchGeneration++
      this._inflightFetch = null
      this._queuedFetch = null
      this.loading = false
    },

    _invalidateFetches({ clearRows = false } = {}) {
      this._invalidateListFetches()
      this._scopeGeneration++
      this._resumePromises = {}
      this._endPromises = {}
      if (clearRows) this.rows = []
    },

    markRuntimeStopped(runtimeId) {
      if (!runtimeId) return
      // Invalidate any request that started before the successful Stop.
      // Its live snapshot must not resurrect the row after the local
      // liveness transition.
      this._invalidateListFetches()
      this.rows = this.rows.map((row) => {
        const matches = row.runtime_id === runtimeId || (row.is_live && row.id === runtimeId)
        if (!matches) return row
        return { ...row, runtime_id: null, is_live: false, status: "paused" }
      })
    },

    async resume(row) {
      const resumeScope = this._syncHostScope()
      if (row._hostScope && row._hostScope !== resumeScope) {
        throw new Error("Conversation belongs to a different host")
      }
      if (row.is_live && row.runtime_id) return row.runtime_id
      if (!row.saved_name) throw new Error("Conversation has no saved session to resume")

      const scopeGeneration = this._scopeGeneration
      const key = `${resumeScope}:${row.conversation_id || row.id}`
      if (this._endPromises[key]) throw new Error("Conversation is still ending")
      const existing = this._resumePromises[key]
      if (existing) return existing

      const task = (async () => {
        const tabs = useTabsStore()
        const instances = useInstancesStore()
        const runtimeId = await tabs.createSession({
          kind: "resume",
          sessionName: row.saved_name,
          attachMode: "none",
          onNode: row.node_id || "_host",
        })
        if (runtimeId === null) return null
        this._syncHostScope()
        if (scopeGeneration !== this._scopeGeneration || resumeScope !== this._hostScope) {
          throw new Error("Conversation host changed while resuming")
        }
        await Promise.all([instances.fetchAll(), this.fetchAll({ force: true })])
        this._syncHostScope()
        if (scopeGeneration !== this._scopeGeneration || resumeScope !== this._hostScope) {
          throw new Error("Conversation host changed while resuming")
        }
        return runtimeId
      })()

      this._resumePromises[key] = task
      try {
        return await task
      } finally {
        if (this._resumePromises[key] === task) delete this._resumePromises[key]
      }
    },

    async endConversation(row) {
      const endScope = this._syncHostScope()
      if (row._hostScope && row._hostScope !== endScope) {
        throw new Error("Conversation belongs to a different host")
      }
      const scopeGeneration = this._scopeGeneration
      const key = `${endScope}:${row.conversation_id || row.id}`
      if (this._resumePromises[key]) throw new Error("Conversation is still resuming")
      if (this._endPromises[key]) return this._endPromises[key]
      let task
      task = (async () => {
        await sessionAPI.endConversation(row.conversation_id || row.id)
        this._syncHostScope()
        if (scopeGeneration !== this._scopeGeneration || endScope !== this._hostScope) {
          throw new Error("Conversation host changed while ending")
        }
        const instances = useInstancesStore()
        await Promise.all([instances.fetchAll(), this.fetchAll({ force: true })])
        this._syncHostScope()
        if (scopeGeneration !== this._scopeGeneration || endScope !== this._hostScope) {
          throw new Error("Conversation host changed while ending")
        }
      })().finally(() => {
        if (this._endPromises[key] === task) delete this._endPromises[key]
      })
      this._endPromises[key] = task
      return task
    },

    async openSurface(row, surface) {
      const runtimeId = await this.resume(row)
      if (runtimeId === null) return null
      const tabs = useTabsStore()
      await tabs.openSurface(runtimeId, surface, {
        config_name: row.config_name,
        type: row.type,
      })
      return runtimeId
    },

    startPolling() {
      this._subscribers++
      if (this._pollInterval !== null) return

      this._syncHostScope()
      const hosts = useHostsStore()
      const refreshScope = () => {
        const previousScope = this._hostScope
        const nextScope = this._syncHostScope()
        if (nextScope !== previousScope && this._subscribers > 0) this.fetchAll()
      }
      this._unsubscribeHosts = hosts.$subscribe(refreshScope)
      this._unsubscribeAuth = useAuthStore().$subscribe(refreshScope)
      this._pollInterval = createVisibilityInterval(() => this.fetchAll(), POLL_INTERVAL_MS, {
        immediate: true,
      })
      this._pollInterval.start()
    },

    stopPolling() {
      this._subscribers = Math.max(0, this._subscribers - 1)
      if (this._subscribers > 0) return

      if (this._pollInterval !== null) {
        this._pollInterval.stop()
        this._pollInterval = null
      }
      if (this._unsubscribeHosts) {
        this._unsubscribeHosts()
        this._unsubscribeHosts = null
      }
      if (this._unsubscribeAuth) {
        this._unsubscribeAuth()
        this._unsubscribeAuth = null
      }
      this._invalidateFetches()
    },
  },
})

function getHostScope() {
  const hosts = useHostsStore()
  const user = hosts.activeUser
  const auth = useAuthStore()
  const sameOriginUser = auth.sameOriginUser
  const userScope =
    user?.id ||
    user?.username ||
    hosts.activeUserToken ||
    sameOriginUser?.id ||
    sameOriginUser?.username ||
    "__anonymous__"
  return `${hosts.activeHostId || SAME_ORIGIN_SCOPE}:${userScope}`
}

function mapConversation(data, hostScope) {
  return {
    id: data.id,
    conversation_id: data.conversation_id || data.id,
    runtime_id: data.runtime_id || null,
    saved_name: data.saved_name || null,
    config_name: data.config_name || data.saved_name || data.id,
    type: data.type,
    status: data.status,
    is_live: Boolean(data.is_live),
    pwd: data.pwd || "",
    node_id: data.node_id || "_host",
    creatures: data.creatures || [],
    last_active: data.last_active || null,
    _hostScope: hostScope,
  }
}
