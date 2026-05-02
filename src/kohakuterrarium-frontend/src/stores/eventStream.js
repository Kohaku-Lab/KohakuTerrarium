/**
 * Event-stream store — events for one (session, agent, turn) trio.
 *
 * Cursor-paginated against ``GET /sessions/{n}/events``. The store
 * caches the events for the *currently expanded* turn only — switching
 * to a different turn clears and refetches. This keeps memory bounded
 * even on long sessions where one turn might have thousands of events.
 *
 * **Per-scope** (scope = session name). The session-viewer macro-tab
 * provides the session name as scope, so two viewers don't trample
 * each other's loaded events / cursor.
 */

import { defineStore } from "pinia"
import { getCurrentInstance } from "vue"

import { injectScope, registerScopeDisposer } from "@/composables/useScope"
import { sessionAPI } from "@/utils/api"

const _eventStreamOptions = {
  state: () => ({
    sessionName: "",
    agent: "",
    turnIndex: null,
    events: [],
    nextCursor: null,
    loading: false,
    error: "",
  }),

  getters: {
    hasMore: (state) => state.nextCursor !== null,
  },

  actions: {
    async loadTurn(sessionName, { agent = null, turnIndex = null } = {}) {
      this.sessionName = sessionName
      this.agent = agent || ""
      this.turnIndex = turnIndex
      this.events = []
      this.nextCursor = null
      this.error = ""
      await this.loadMore()
    },

    async loadMore() {
      if (!this.sessionName) return
      if (this.loading) return
      this.loading = true
      try {
        const data = await sessionAPI.getEvents(this.sessionName, {
          agent: this.agent || null,
          turnIndex: this.turnIndex,
          limit: 200,
          cursor: this.nextCursor,
        })
        const incoming = data.events || []
        if (this.nextCursor === null) {
          this.events = incoming
        } else {
          this.events.push(...incoming)
        }
        this.nextCursor = data.next_cursor ?? null
      } catch (err) {
        this.error = `Failed to load events: ${err.message || err}`
      } finally {
        this.loading = false
      }
    },

    appendLive(eventObj) {
      if (!eventObj) return
      if (
        this.turnIndex != null &&
        eventObj.turn_index != null &&
        eventObj.turn_index !== this.turnIndex
      ) {
        return
      }
      this.events.push(eventObj)
    },

    clear() {
      this.sessionName = ""
      this.agent = ""
      this.turnIndex = null
      this.events = []
      this.nextCursor = null
      this.error = ""
    },
  },
}

const _eventStreamFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _eventStreamFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`eventStream:${key}`, _eventStreamOptions)
    _eventStreamFactories.set(key, useFn)
    if (scope) {
      registerScopeDisposer(scope, () => {
        try {
          useFn().$dispose?.()
        } catch {
          /* swallow */
        }
        _eventStreamFactories.delete(key)
      })
    }
  }
  return useFn
}

export function useEventStreamStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) return _factoryFor(injectScope())()
  return _factoryFor(null)()
}
