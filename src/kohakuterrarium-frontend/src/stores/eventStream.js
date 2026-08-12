/**
 * Event-stream store — events for one (session, agent, turn) trio.
 *
 * Cursor-paginated against ``GET /sessions/{n}/events``. The store
 * caches the events for the *currently expanded* turn only — switching
 * to a different turn clears and refetches. This keeps memory bounded
 * even on long sessions where one turn might have thousands of events.
 * Trace groups use one ephemeral store per mounted turn, so multiple
 * expanded turns do not replace each other's cached events.
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
    loadGeneration: 0,
    loadingGeneration: null,
    error: "",
  }),

  getters: {
    hasMore: (state) => state.nextCursor !== null,
  },

  actions: {
    async loadTurn(sessionName, { agent = null, turnIndex = null } = {}) {
      this.loadGeneration += 1
      const generation = this.loadGeneration
      this.sessionName = sessionName
      this.agent = agent || ""
      this.turnIndex = turnIndex
      this.events = []
      this.nextCursor = null
      this.error = ""
      await this.loadMore(generation)
    },

    async loadMore(generation = this.loadGeneration) {
      if (!this.sessionName) return
      if (this.loadingGeneration === generation) return
      const sessionName = this.sessionName
      const agent = this.agent
      const turnIndex = this.turnIndex
      const cursor = this.nextCursor
      this.loading = true
      this.loadingGeneration = generation
      try {
        const data = await sessionAPI.getEvents(sessionName, {
          agent: agent || null,
          turnIndex,
          limit: 200,
          cursor,
        })
        if (generation !== this.loadGeneration) return
        const incoming = data.events || []
        if (cursor === null) {
          this.events = incoming
        } else {
          this.events.push(...incoming)
        }
        this.nextCursor = data.next_cursor ?? null
      } catch (err) {
        if (generation !== this.loadGeneration) return
        this.error = `Failed to load events: ${err.message || err}`
      } finally {
        if (this.loadingGeneration === generation) {
          this.loading = false
          this.loadingGeneration = null
        }
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
      this.loadGeneration += 1
      this.sessionName = ""
      this.agent = ""
      this.turnIndex = null
      this.events = []
      this.nextCursor = null
      this.loading = false
      this.loadingGeneration = null
      this.error = ""
    },
  },
}

const _eventStreamFactories = new Map()

function _factoryFor(scope, registerWithScope = true) {
  const key = scope || "default"
  let useFn = _eventStreamFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`eventStream:${key}`, _eventStreamOptions)
    _eventStreamFactories.set(key, useFn)
    if (scope && registerWithScope) {
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

export function useEphemeralEventStreamStore(scope) {
  return _factoryFor(scope, false)()
}

export function disposeEventStreamStore(scope) {
  const key = scope || "default"
  const useFn = _eventStreamFactories.get(key)
  if (!useFn) return
  try {
    useFn().$dispose?.()
  } catch {
    /* swallow */
  }
  _eventStreamFactories.delete(key)
}
