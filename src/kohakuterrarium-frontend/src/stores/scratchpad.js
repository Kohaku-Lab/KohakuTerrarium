/**
 * Scratchpad store — talks to the Phase 1 read-only API to fetch and
 * patch an agent's scratchpad. Fetch-on-demand only (no polling).
 */

import { defineStore } from "pinia"
import { ref } from "vue"

import { agentAPI, terrariumAPI } from "@/utils/api"

export const useScratchpadStore = defineStore("scratchpad", () => {
  const byAgent = ref(/** @type {Record<string, Record<string, string>>} */ ({}))
  const loading = ref(/** @type {Record<string, boolean>} */ ({}))
  const error = ref(/** @type {Record<string, string>} */ ({}))

  async function fetch(agentId, target = null) {
    if (!agentId) return
    const key = target ? `${agentId}:${target}` : agentId
    loading.value = { ...loading.value, [key]: true }
    try {
      const data = target
        ? await terrariumAPI.getScratchpad(agentId, target)
        : await agentAPI.getScratchpad(agentId)
      byAgent.value = { ...byAgent.value, [key]: data }
      const next = { ...error.value }
      delete next[key]
      error.value = next
    } catch (err) {
      error.value = { ...error.value, [key]: String(err?.message || err) }
    } finally {
      loading.value = { ...loading.value, [key]: false }
    }
  }

  async function patch(agentId, updates, target = null) {
    if (!agentId) return
    const key = target ? `${agentId}:${target}` : agentId
    const data = target
      ? await terrariumAPI.patchScratchpad(agentId, target, updates)
      : await agentAPI.patchScratchpad(agentId, updates)
    byAgent.value = { ...byAgent.value, [key]: data }
    return data
  }

  function getFor(agentId, target = null) {
    const key = target ? `${agentId}:${target}` : agentId
    return byAgent.value[key] || {}
  }

  return { byAgent, loading, error, fetch, patch, getFor }
})
