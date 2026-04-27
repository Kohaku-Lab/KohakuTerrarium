/**
 * Event-stream store — events for one (session, agent, turn) trio.
 *
 * Cursor-paginated against ``GET /sessions/{n}/events``. The store
 * caches the events for the *currently expanded* turn only — switching
 * to a different turn clears and refetches. This keeps memory bounded
 * even on long sessions where one turn might have thousands of events.
 */

import { defineStore } from "pinia"

import { sessionAPI } from "@/utils/api"

export const useEventStreamStore = defineStore("eventStream", {
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
    /** Load (or replace) events for one turn. */
    async loadTurn(sessionName, { agent = null, turnIndex = null } = {}) {
      this.sessionName = sessionName
      this.agent = agent || ""
      this.turnIndex = turnIndex
      this.events = []
      this.nextCursor = null
      this.error = ""
      await this.loadMore()
    },

    /** Cursor-paginated next-page fetch. Idempotent past end-of-data. */
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

    /** Append a single event from the live-attach stream. */
    appendLive(eventObj) {
      if (!eventObj) return
      // Only keep live events that match the currently displayed turn.
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
})
