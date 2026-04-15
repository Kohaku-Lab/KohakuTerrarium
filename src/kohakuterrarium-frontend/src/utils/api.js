/**
 * API client for KohakuTerrarium backend.
 */

import axios from "axios"

function encodeTarget(target) {
  return encodeURIComponent(target)
}

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
})

/**
 * @typedef {{ name: string, path: string, description: string }} ConfigItem
 * @typedef {{ id: string, type: string, config_name: string, config_path: string, pwd: string, status: string, has_root: boolean, creatures: object[], channels: object[], created_at: string }} InstanceInfo
 * @typedef {{ id: string, role: string, content: string, timestamp: string, sender?: string, tool_calls?: object[] }} ChatMessage
 */

/** Config discovery */
export const configAPI = {
  /** @returns {Promise<ConfigItem[]>} */
  async listCreatures() {
    const { data } = await api.get("/configs/creatures")
    return data
  },

  /** @returns {Promise<ConfigItem[]>} */
  async listTerrariums() {
    const { data } = await api.get("/configs/terrariums")
    return data
  },

  /** @returns {Promise<{cwd: string, platform: string}>} */
  async getServerInfo() {
    const { data } = await api.get("/configs/server-info")
    return data
  },

  /** @returns {Promise<{id: string, name: string, available: boolean}[]>} */
  async getModels() {
    const { data } = await api.get("/configs/models")
    return data
  },

  /** @returns {Promise<{name: string, description: string}[]>} */
  async getCommands() {
    const { data } = await api.get("/configs/commands")
    return data
  },
}

/** Terrarium lifecycle */
export const terrariumAPI = {
  /** @returns {Promise<{terrarium_id: string}>} */
  async create(configPath, pwd) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    const { data } = await api.post("/terrariums", body)
    return data
  },

  /** @returns {Promise<object[]>} */
  async list() {
    const { data } = await api.get("/terrariums")
    return data
  },

  /** @returns {Promise<object>} */
  async get(id) {
    const { data } = await api.get(`/terrariums/${id}`)
    return data
  },

  async stop(id) {
    await api.delete(`/terrariums/${id}`)
  },

  /** @returns {Promise<object[]>} */
  async listChannels(id) {
    const { data } = await api.get(`/terrariums/${id}/channels`)
    return data
  },

  async sendToChannel(id, channelName, content, sender = "human") {
    const { data } = await api.post(`/terrariums/${id}/channels/${channelName}/send`, {
      content,
      sender,
    })
    return data
  },

  /**
   * Get full history for a creature/root in a terrarium.
   * Returns { messages: [...], events: [...] }
   */
  async getHistory(id, target) {
    const { data } = await api.get(`/terrariums/${id}/history/${encodeTarget(target)}`)
    return data
  },

  async interruptCreature(id, name) {
    const { data } = await api.post(`/terrariums/${id}/creatures/${name}/interrupt`)
    return data
  },

  async listCreatureJobs(id, name) {
    const { data } = await api.get(`/terrariums/${id}/creatures/${name}/jobs`)
    return data
  },

  async promoteCreatureTask(id, name, jobId) {
    const { data } = await api.post(`/terrariums/${id}/creatures/${name}/promote/${jobId}`)
    return data
  },

  async stopCreatureTask(id, name, jobId) {
    const { data } = await api.post(`/terrariums/${id}/creatures/${name}/tasks/${jobId}/stop`)
    return data
  },

  async switchCreatureModel(id, name, model) {
    const { data } = await api.post(`/terrariums/${id}/creatures/${name}/model`, { model })
    return data
  },

  /** Execute a slash command on a terrarium creature */
  async executeCreatureCommand(id, name, command, args = "") {
    const { data } = await api.post(`/terrariums/${id}/creatures/${name}/command`, {
      command,
      args,
    })
    return data
  },

  async getScratchpad(id, target) {
    const { data } = await api.get(`/terrariums/${id}/scratchpad/${encodeTarget(target)}`)
    return data
  },

  async patchScratchpad(id, target, updates) {
    const { data } = await api.patch(`/terrariums/${id}/scratchpad/${encodeTarget(target)}`, {
      updates,
    })
    return data
  },

  async getEnv(id, target) {
    const { data } = await api.get(`/terrariums/${id}/env/${encodeTarget(target)}`)
    return data
  },

  async listPlugins(id, target) {
    const { data } = await api.get(`/terrariums/${id}/plugins/${encodeTarget(target)}`)
    return data
  },

  async listTriggers(id, target) {
    const { data } = await api.get(`/terrariums/${id}/triggers/${encodeTarget(target)}`)
    return data
  },

  async getSystemPrompt(id, target) {
    const { data } = await api.get(`/terrariums/${id}/system-prompt/${encodeTarget(target)}`)
    return data
  },
}

/** Standalone agent lifecycle */
export const agentAPI = {
  /** @returns {Promise<{agent_id: string}>} */
  async create(configPath, pwd) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    const { data } = await api.post("/agents", body)
    return data
  },

  /** @returns {Promise<object[]>} */
  async list() {
    const { data } = await api.get("/agents")
    return data
  },

  /** @returns {Promise<object>} */
  async get(id) {
    const { data } = await api.get(`/agents/${id}`)
    return data
  },

  async stop(id) {
    await api.delete(`/agents/${id}`)
  },

  async interrupt(id) {
    const { data } = await api.post(`/agents/${id}/interrupt`)
    return data
  },

  /** Get conversation history + event log */
  async getHistory(id) {
    const { data } = await api.get(`/agents/${id}/history`)
    return data
  },

  /** Non-streaming chat */
  async chat(id, message) {
    const { data } = await api.post(`/agents/${id}/chat`, { message })
    return data
  },

  async listJobs(id) {
    const { data } = await api.get(`/agents/${id}/jobs`)
    return data
  },

  async stopTask(id, jobId) {
    const { data } = await api.post(`/agents/${id}/tasks/${jobId}/stop`)
    return data
  },

  /** Promote a running direct task to background */
  async promote(id, jobId) {
    const { data } = await api.post(`/agents/${id}/promote/${jobId}`)
    return data
  },

  /** List plugins with enabled/disabled status */
  async listPlugins(id) {
    const { data } = await api.get(`/agents/${id}/plugins`)
    return data
  },

  /** Toggle a plugin's enabled state */
  async togglePlugin(id, pluginName) {
    const { data } = await api.post(`/agents/${id}/plugins/${pluginName}/toggle`)
    return data
  },

  /** Regenerate the last assistant response */
  async regenerate(id) {
    const { data } = await api.post(`/agents/${id}/regenerate`)
    return data
  },

  /** Edit a user message at a given index and re-run */
  async editMessage(id, msgIdx, content) {
    const { data } = await api.post(`/agents/${id}/messages/${msgIdx}/edit`, {
      content,
    })
    return data
  },

  /** Rewind conversation to a point (drop messages onward) */
  async rewindTo(id, msgIdx) {
    const { data } = await api.post(`/agents/${id}/messages/${msgIdx}/rewind`)
    return data
  },

  /** Switch the model for a running agent */
  async switchModel(id, model) {
    const { data } = await api.post(`/agents/${id}/model`, { model })
    return data
  },

  /** Execute a slash command on an agent */
  async executeCommand(id, command, args = "") {
    const { data } = await api.post(`/agents/${id}/command`, { command, args })
    return data
  },

  // ── Phase 1 read-only inspection endpoints ───────────────────────

  /** @returns {Promise<Record<string, string>>} */
  async getScratchpad(id) {
    const { data } = await api.get(`/agents/${id}/scratchpad`)
    return data
  },

  /**
   * Patch the scratchpad. Values may be `null` to delete a key.
   * @param {string} id
   * @param {Record<string, string | null>} updates
   */
  async patchScratchpad(id, updates) {
    const { data } = await api.patch(`/agents/${id}/scratchpad`, { updates })
    return data
  },

  /** @returns {Promise<{trigger_id: string, trigger_type: string, running: boolean, created_at: string}[]>} */
  async listTriggers(id) {
    const { data } = await api.get(`/agents/${id}/triggers`)
    return data
  },

  /** @returns {Promise<{pwd: string, env: Record<string, string>}>} */
  async getEnv(id) {
    const { data } = await api.get(`/agents/${id}/env`)
    return data
  },

  /** @returns {Promise<{text: string}>} */
  async getSystemPrompt(id) {
    const { data } = await api.get(`/agents/${id}/system-prompt`)
    return data
  },
}

/** File operations */
export const filesAPI = {
  async getTree(root, depth = 3) {
    const { data } = await api.get("/files/tree", { params: { root, depth } })
    return data
  },

  async readFile(path) {
    const { data } = await api.get("/files/read", { params: { path } })
    return data
  },

  async writeFile(path, content) {
    const { data } = await api.post("/files/write", { path, content })
    return data
  },
}

/** Saved sessions */
export const sessionAPI = {
  async list({ limit = 20, offset = 0, search = "", refresh = false } = {}) {
    const params = { limit, offset }
    if (search) params.search = search
    if (refresh) params.refresh = true
    const { data } = await api.get("/sessions", { params })
    return data
  },

  /** @returns {Promise<{instance_id: string, type: string, session_name: string}>} */
  async resume(sessionName) {
    const { data } = await api.post(`/sessions/${sessionName}/resume`)
    return data
  },

  /**
   * Search a saved session's memory (Phase 1 read-only endpoint).
   * @param {string} sessionName
   * @param {{q: string, mode?: string, k?: number, agent?: string}} opts
   */
  async searchMemory(sessionName, { q, mode = "auto", k = 10, agent = null } = {}) {
    const params = { q, mode, k }
    if (agent) params.agent = agent
    const { data } = await api.get(`/sessions/${sessionName}/memory/search`, {
      params,
    })
    return data
  },

  async delete(sessionName) {
    const { data } = await api.delete(`/sessions/${sessionName}`)
    return data
  },
}

/** Settings - API keys, custom models */
export const settingsAPI = {
  async getKeys() {
    const { data } = await api.get("/settings/keys")
    return data
  },
  async saveKey(provider, key) {
    const { data } = await api.post("/settings/keys", { provider, key })
    return data
  },
  async removeKey(provider) {
    const { data } = await api.delete(`/settings/keys/${provider}`)
    return data
  },
  async getProfiles() {
    const { data } = await api.get("/settings/profiles")
    return data
  },
  async saveProfile(profile) {
    const { data } = await api.post("/settings/profiles", profile)
    return data
  },
  async deleteProfile(name) {
    const { data } = await api.delete(`/settings/profiles/${name}`)
    return data
  },
  async getDefaultModel() {
    const { data } = await api.get("/settings/default-model")
    return data
  },
  async setDefaultModel(name) {
    const { data } = await api.post("/settings/default-model", { name })
    return data
  },
  // MCP server management
  async listMCP() {
    const { data } = await api.get("/settings/mcp")
    return data
  },
  async addMCP(server) {
    const { data } = await api.post("/settings/mcp", server)
    return data
  },
  async removeMCP(name) {
    const { data } = await api.delete(`/settings/mcp/${name}`)
    return data
  },
  async getCodexUsage() {
    const { data } = await api.get("/settings/codex-usage")
    return data
  },
}

/** Registry browser */
export const registryAPI = {
  async listLocal() {
    const { data } = await api.get("/registry")
    return data
  },
  async listRemote() {
    const { data } = await api.get("/registry/remote")
    return data
  },
  async install(url, name) {
    const { data } = await api.post("/registry/install", { url, name })
    return data
  },
  async uninstall(name) {
    const { data } = await api.post("/registry/uninstall", { name })
    return data
  },
}

export default api
