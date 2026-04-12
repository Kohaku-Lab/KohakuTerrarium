/**
 * Scratchpad store — talks to the Phase 1 read-only API to fetch and
 * patch an agent's scratchpad. Fetch-on-demand only (no polling).
 */

import { defineStore } from "pinia"
import { ref } from "vue"

import { agentAPI } from "@/utils/api"

export const useScratchpadStore = defineStore("scratchpad", () => {
  const byAgent = ref(/** @type {Record<string, Record<string, string>>} */ ({}))
  const loading = ref(/** @type {Record<string, boolean>} */ ({}))
  const error = ref(/** @type {Record<string, string>} */ ({}))

  async function fetch(agentId) {
    if (!agentId) return
    loading.value = { ...loading.value, [agentId]: true }
    try {
      const data = await agentAPI.getScratchpad(agentId)
      byAgent.value = { ...byAgent.value, [agentId]: data }
      const next = { ...error.value }
      delete next[agentId]
      error.value = next
    } catch (err) {
      error.value = { ...error.value, [agentId]: String(err?.message || err) }
    } finally {
      loading.value = { ...loading.value, [agentId]: false }
    }
  }

  async function patch(agentId, updates) {
    if (!agentId) return
    const data = await agentAPI.patchScratchpad(agentId, updates)
    byAgent.value = { ...byAgent.value, [agentId]: data }
    return data
  }

  function getFor(agentId) {
    return byAgent.value[agentId] || {}
  }

  return { byAgent, loading, error, fetch, patch, getFor }
})
