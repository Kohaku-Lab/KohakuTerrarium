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

  /** @returns {Promise<{name: string, model: string, provider: string, available: boolean, variation_groups?: Record<string, Record<string, object>>, selected_variations?: Record<string, string>}[]>} */
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

/** Runtime graph snapshot for the graph editor. */
export const runtimeGraphAPI = {
  async snapshot() {
    const { data } = await api.get("/runtime/graph")
    return data
  },
}

/** Terrarium lifecycle */
export const terrariumAPI = {
  /** @returns {Promise<{terrarium_id: string}>} */
  async create(configPath, pwd, name = null) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    if (name) body.name = name
    const { data } = await api.post("/sessions/active/terrariums", body)
    return data
  },

  async rename(id, name) {
    const { data } = await api.post(`/sessions/active/terrariums/${encodeTarget(id)}/rename`, {
      name,
    })
    return data
  },

  /** @returns {Promise<object[]>} */
  async list() {
    const { data } = await api.get("/sessions/active/terrariums")
    return data
  },

  /** @returns {Promise<object>} */
  async get(id) {
    const { data } = await api.get(`/sessions/active/terrariums/${id}`)
    return data
  },

  async stop(id) {
    await api.delete(`/sessions/active/terrariums/${id}`)
  },

  /** @returns {Promise<object[]>} */
  async listChannels(id) {
    const { data } = await api.get(`/sessions/topology/${id}/channels`)
    return data
  },

  async addChannel(id, name, channelType = "queue", description = "") {
    const { data } = await api.post(`/sessions/topology/${encodeTarget(id)}/channels`, {
      name,
      channel_type: channelType,
      description,
    })
    return data
  },

  /** Merge graph ``b`` into graph ``a`` so both creature sets share
   * one engine graph. Returns ``{session_id, merged}`` where
   * ``session_id`` is the surviving graph id. No bridge channel is
   * created — used when wiring a channel that lives in a different
   * molecule from the creature being wired to it. */
  async mergeGraphs(aSessionId, bSessionId) {
    const { data } = await api.post(
      `/sessions/topology/${encodeTarget(aSessionId)}/merge/${encodeTarget(bSessionId)}`,
    )
    return data
  },

  async sendToChannel(id, channelName, content, sender = "human") {
    const { data } = await api.post(
      `/sessions/topology/${encodeTarget(id)}/channels/${encodeURIComponent(channelName)}/send`,
      {
        content,
        sender,
      },
    )
    return data
  },

  async connect(id, sender, receiver, channel = null, channelType = "queue") {
    const body = { sender, receiver, channel_type: channelType }
    if (channel) body.channel = channel
    const { data } = await api.post(`/sessions/topology/${encodeTarget(id)}/connect`, body)
    return data
  },

  async disconnect(id, sender, receiver, channel = null) {
    const body = { sender, receiver }
    if (channel) body.channel = channel
    const { data } = await api.post(`/sessions/topology/${encodeTarget(id)}/disconnect`, body)
    return data
  },

  async wireCreature(id, creatureId, channelName, direction) {
    const { data } = await api.post(
      `/sessions/topology/${encodeTarget(id)}/creatures/${encodeTarget(creatureId)}/wire`,
      {
        channel: channelName,
        direction,
      },
    )
    return data
  },

  async unwireCreature(id, creatureId, channelName, direction) {
    const { data } = await api.delete(
      `/sessions/topology/${encodeTarget(id)}/creatures/${encodeTarget(creatureId)}/wire`,
      {
        data: {
          channel: channelName,
          direction,
        },
      },
    )
    return data
  },

  /**
   * Get full history for a creature/root in a terrarium.
   * Returns { messages: [...], events: [...] }
   */
  async getHistory(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/history`)
    return data
  },

  async interruptCreature(id, name) {
    const { data } = await api.post(`/sessions/${id}/creatures/${encodeTarget(name)}/interrupt`)
    return data
  },

  async listCreatureJobs(id, name) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(name)}/jobs`)
    return data
  },

  async promoteCreatureTask(id, name, jobId) {
    const { data } = await api.post(
      `/sessions/${id}/creatures/${encodeTarget(name)}/promote/${jobId}`,
    )
    return data
  },

  async stopCreatureTask(id, name, jobId) {
    const { data } = await api.post(
      `/sessions/${id}/creatures/${encodeTarget(name)}/tasks/${jobId}/stop`,
    )
    return data
  },

  async switchCreatureModel(id, name, model) {
    const { data } = await api.post(`/sessions/${id}/creatures/${encodeTarget(name)}/model`, {
      model,
    })
    return data
  },

  /** Execute a slash command on a terrarium creature */
  async executeCreatureCommand(id, name, command, args = "") {
    const { data } = await api.post(`/sessions/${id}/creatures/${encodeTarget(name)}/command`, {
      command,
      args,
    })
    return data
  },

  async getScratchpad(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/scratchpad`)
    return data
  },

  async patchScratchpad(id, target, updates) {
    const { data } = await api.patch(
      `/sessions/${id}/creatures/${encodeTarget(target)}/scratchpad`,
      {
        updates,
      },
    )
    return data
  },

  async getEnv(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/env`)
    return data
  },

  async listPlugins(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/plugins`)
    return data
  },

  async togglePlugin(id, target, pluginName) {
    const { data } = await api.post(
      `/sessions/${id}/creatures/${encodeTarget(target)}/plugins/${encodeURIComponent(pluginName)}/toggle`,
    )
    return data
  },

  async listTriggers(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/triggers`)
    return data
  },

  async getSystemPrompt(id, target) {
    const { data } = await api.get(
      `/sessions/${id}/creatures/${encodeTarget(target)}/system-prompt`,
    )
    return data
  },
}

/** Standalone agent lifecycle */
export const agentAPI = {
  /** @returns {Promise<{agent_id: string}>} */
  async create(configPath, pwd, name = null) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    if (name) body.name = name
    const { data } = await api.post("/sessions/active/agents", body)
    return data
  },

  async rename(creatureId, name) {
    const { data } = await api.post(`/sessions/active/agents/${encodeTarget(creatureId)}/rename`, {
      name,
    })
    return data
  },

  /** Rename a creature inside a multi-creature session. */
  async renameWithin(sessionId, creatureId, name) {
    const { data } = await api.post(
      `/sessions/active/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/rename`,
      { name },
    )
    return data
  },

  /** @returns {Promise<object[]>} */
  async list() {
    const { data } = await api.get("/sessions/active/agents")
    return data
  },

  /** @returns {Promise<object>} */
  async get(id) {
    const { data } = await api.get(`/sessions/active/agents/${id}`)
    return data
  },

  async stop(id) {
    await api.delete(`/sessions/active/agents/${id}`)
  },

  async interrupt(id) {
    const { data } = await api.post(`/sessions/_/creatures/${id}/interrupt`)
    return data
  },

  /** Get conversation history + event log */
  async getHistory(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/history`)
    return data
  },

  /** Non-streaming chat */
  async chat(id, message) {
    const body = Array.isArray(message) ? { content: message } : { message }
    const { data } = await api.post(`/sessions/_/creatures/${id}/chat`, body)
    return data
  },

  async listJobs(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/jobs`)
    return data
  },

  async stopTask(id, jobId) {
    const { data } = await api.post(`/sessions/_/creatures/${id}/tasks/${jobId}/stop`)
    return data
  },

  /** Promote a running direct task to background */
  async promote(id, jobId) {
    const { data } = await api.post(`/sessions/_/creatures/${id}/promote/${jobId}`)
    return data
  },

  /** List plugins with enabled/disabled status */
  async listPlugins(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/plugins`)
    return data
  },

  /** Toggle a plugin's enabled state */
  async togglePlugin(id, pluginName) {
    const { data } = await api.post(
      `/sessions/_/creatures/${id}/plugins/${encodeURIComponent(pluginName)}/toggle`,
    )
    return data
  },

  /** Regenerate the last assistant response.
   *
   * ``sessionId`` is the terrarium's session id (or ``"_"`` for a
   * standalone agent). ``creatureId`` is the target creature/agent
   * name. Old call sites that pass only an agent id can still call
   * ``regenerate(agentId)`` — the second arg defaults to the first.
   */
  async regenerate(sessionId, creatureId) {
    const sid = sessionId || "_"
    const cid = creatureId || sessionId
    const { data } = await api.post(
      `/sessions/${encodeTarget(sid)}/creatures/${encodeTarget(cid)}/regenerate`,
    )
    return data
  },

  /** Edit a user message at a given index and re-run */
  async editMessage(sessionId, creatureId, msgIdx, content, target = {}) {
    const body = { content }
    if (target.turnIndex != null) body.turn_index = target.turnIndex
    if (target.userPosition != null) body.user_position = target.userPosition
    const sid = sessionId || "_"
    const cid = creatureId || sessionId
    const { data } = await api.post(
      `/sessions/${encodeTarget(sid)}/creatures/${encodeTarget(cid)}/messages/${msgIdx}/edit`,
      body,
    )
    return data
  },

  /** Rewind conversation to a point (drop messages onward) */
  async rewindTo(sessionId, creatureId, msgIdx) {
    const sid = sessionId || "_"
    const cid = creatureId || sessionId
    const { data } = await api.post(
      `/sessions/${encodeTarget(sid)}/creatures/${encodeTarget(cid)}/messages/${msgIdx}/rewind`,
    )
    return data
  },

  /** Switch the model for a running agent */
  async switchModel(id, model) {
    const { data } = await api.post(`/sessions/_/creatures/${id}/model`, { model })
    return data
  },

  /** Execute a slash command on an agent */
  async executeCommand(id, command, args = "") {
    const { data } = await api.post(`/sessions/_/creatures/${id}/command`, {
      command,
      args,
    })
    return data
  },

  // ── Phase 1 read-only inspection endpoints ───────────────────────

  /** @returns {Promise<Record<string, string>>} */
  async getScratchpad(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/scratchpad`)
    return data
  },

  /**
   * Patch the scratchpad. Values may be `null` to delete a key.
   * @param {string} id
   * @param {Record<string, string | null>} updates
   */
  async patchScratchpad(id, updates) {
    const { data } = await api.patch(`/sessions/_/creatures/${id}/scratchpad`, {
      updates,
    })
    return data
  },

  /** @returns {Promise<{trigger_id: string, trigger_type: string, running: boolean, created_at: string}[]>} */
  async listTriggers(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/triggers`)
    return data
  },

  /** @returns {Promise<{pwd: string, env: Record<string, string>}>} */
  async getEnv(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/env`)
    return data
  },

  /** @returns {Promise<{text: string}>} */
  async getSystemPrompt(id) {
    const { data } = await api.get(`/sessions/_/creatures/${id}/system-prompt`)
    return data
  },
}

/**
 * Per-creature configurable modules — unified runtime config surface
 * across plugins, provider-native tools, and any future module type.
 *
 * Backend: /api/sessions/{sid}/creatures/{cid}/modules{/{type}/{name}/...}
 *
 * For standalone agents, ``sid="_"`` and ``creatureId`` is the agent id.
 * For terrarium-attached creatures, ``sid`` is the terrarium id and
 * ``creatureId`` is the per-target id.
 */
export const moduleAPI = {
  /** List every configurable module (any type) on this creature. */
  async list(sessionId, creatureId) {
    const { data } = await api.get(
      `/sessions/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/modules`,
    )
    return data?.modules || []
  },

  /** Read schema + current values for one module. */
  async getOptions(sessionId, creatureId, moduleType, name) {
    const { data } = await api.get(
      `/sessions/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/modules/${encodeURIComponent(moduleType)}/${encodeURIComponent(name)}/options`,
    )
    return data
  },

  /** Apply runtime option overrides to one module. */
  async setOptions(sessionId, creatureId, moduleType, name, values) {
    const { data } = await api.put(
      `/sessions/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/modules/${encodeURIComponent(moduleType)}/${encodeURIComponent(name)}/options`,
      { values: values || {} },
    )
    return data
  },

  /** Toggle a module's enabled state (only supported for some types — plugin today). */
  async toggle(sessionId, creatureId, moduleType, name) {
    const { data } = await api.post(
      `/sessions/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/modules/${encodeURIComponent(moduleType)}/${encodeURIComponent(name)}/toggle`,
    )
    return data
  },
}

/** Direct runtime output wiring between creatures. */
export const wiringAPI = {
  async listOutputs(sessionId, creatureId) {
    const { data } = await api.get(
      `/sessions/wiring/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/outputs`,
    )
    return data
  },

  async addOutput(sessionId, creatureId, target) {
    const { data } = await api.post(
      `/sessions/wiring/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/outputs`,
      target,
    )
    return data
  },

  async removeOutput(sessionId, creatureId, edgeId) {
    const { data } = await api.delete(
      `/sessions/wiring/${encodeTarget(sessionId)}/creatures/${encodeTarget(creatureId)}/outputs/${encodeURIComponent(edgeId)}`,
    )
    return data
  },
}

/** File operations */
export const filesAPI = {
  async browseDirectories(path = null) {
    const params = {}
    if (path) params.path = path
    const { data } = await api.get("/files/browse", { params })
    return data
  },

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

/** Sessions API — covers both **active runtime sessions** (``listActive``
 *  / ``getActive`` / ``stopActive``) and saved-session lookups (``list`` /
 *  ``resume`` / ``getHistory`` / …). Active sessions all share one shape
 *  regardless of how the session was created (creature config or
 *  terrarium recipe); ``listActive`` is the canonical source for the
 *  dashboard, while the legacy ``agentAPI`` / ``terrariumAPI`` exports
 *  are kept for the per-creature URL methods.
 */
export const sessionAPI = {
  /** Active sessions — list every running session. */
  async listActive() {
    const { data } = await api.get("/sessions/active")
    return data
  },

  /** Active session lookup — accepts either a ``session_id`` or a
   *  ``creature_id``; the backend resolver maps either to the same
   *  session so deep links from before a graph grew past one member
   *  keep working. */
  async getActive(id) {
    const { data } = await api.get(`/sessions/active/${encodeTarget(id)}`)
    return data
  },

  async stopActive(id) {
    await api.delete(`/sessions/active/${encodeTarget(id)}`)
  },

  // ── saved-session lookups ────────────────────────────────────────

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

  async getHistoryIndex(sessionName) {
    const { data } = await api.get(`/sessions/${sessionName}/history`)
    return data
  },

  async getHistory(sessionName, target) {
    const { data } = await api.get(`/sessions/${sessionName}/history/${encodeTarget(target)}`)
    return data
  },

  async delete(sessionName) {
    const { data } = await api.delete(`/sessions/${sessionName}`)
    return data
  },

  // ── V1 Viewer / Trace Viewer endpoints ──────────────────────────

  /**
   * Fork lineage + attached-agent DAG for the session-tree pane.
   * @returns {Promise<{session_name: string, session_id: string, nodes: object[], edges: object[]}>}
   */
  async getTree(sessionName) {
    const { data } = await api.get(`/sessions/${sessionName}/tree`)
    return data
  },

  /**
   * Overview-tab stats. ``agent`` narrows to one creature (default: all).
   */
  async getSummary(sessionName, agent = null) {
    const params = {}
    if (agent) params.agent = agent
    const { data } = await api.get(`/sessions/${sessionName}/summary`, { params })
    return data
  },

  /**
   * Paginated turn-rollup rows. Drives trace timeline + collapsed turn list.
   *
   * Pass ``aggregate: true`` to get per-turn rows summed across every
   * agent in the session, with a ``breakdown`` array of per-agent
   * contributions. ``agent`` is ignored in that mode.
   *
   * @param {string} sessionName
   * @param {{agent?: string, fromTurn?: number, toTurn?: number, limit?: number, offset?: number, aggregate?: boolean}} opts
   */
  async getTurns(
    sessionName,
    {
      agent = null,
      fromTurn = null,
      toTurn = null,
      limit = 200,
      offset = 0,
      aggregate = false,
    } = {},
  ) {
    const params = { limit, offset }
    if (agent) params.agent = agent
    if (fromTurn != null) params.from_turn = fromTurn
    if (toTurn != null) params.to_turn = toTurn
    if (aggregate) params.aggregate = true
    const { data } = await api.get(`/sessions/${sessionName}/turns`, { params })
    return data
  },

  /**
   * Structured diff between two saved sessions.
   */
  async getDiff(sessionName, otherName, agent = null) {
    const params = { other: otherName }
    if (agent) params.agent = agent
    const { data } = await api.get(`/sessions/${sessionName}/diff`, { params })
    return data
  },

  /**
   * Export URL for a session in ``md`` / ``html`` / ``jsonl`` form.
   * Returns a string the browser can navigate to so the standard
   * download flow takes over (the backend sets Content-Disposition).
   */
  exportUrl(sessionName, format = "md", agent = null) {
    const params = new URLSearchParams({ format })
    if (agent) params.set("agent", agent)
    return `/api/sessions/${encodeURIComponent(sessionName)}/export?${params.toString()}`
  },

  /**
   * Filtered events for one agent, cursor-paginated by ``event_id``.
   * @param {string} sessionName
   * @param {{agent?: string, turnIndex?: number, types?: string|string[], fromTs?: number, toTs?: number, limit?: number, cursor?: number}} opts
   */
  async getEvents(
    sessionName,
    {
      agent = null,
      turnIndex = null,
      types = null,
      fromTs = null,
      toTs = null,
      limit = 200,
      cursor = null,
    } = {},
  ) {
    const params = { limit }
    if (agent) params.agent = agent
    if (turnIndex != null) params.turn_index = turnIndex
    if (types) params.types = Array.isArray(types) ? types.join(",") : types
    if (fromTs != null) params.from_ts = fromTs
    if (toTs != null) params.to_ts = toTs
    if (cursor != null) params.cursor = cursor
    const { data } = await api.get(`/sessions/${sessionName}/events`, { params })
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
  async getBackends() {
    const { data } = await api.get("/settings/backends")
    return data
  },
  async saveBackend(backend) {
    const { data } = await api.post("/settings/backends", backend)
    return data
  },
  async deleteBackend(name) {
    const { data } = await api.delete(`/settings/backends/${name}`)
    return data
  },
  async getNativeTools() {
    const { data } = await api.get("/settings/native-tools")
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
  async deleteProfile(name, provider) {
    if (!provider) {
      throw new Error("deleteProfile: provider is required (Phase 3 dropped the bare-name route)")
    }
    const target = `/settings/profiles/${encodeURIComponent(provider)}/${encodeURIComponent(name)}`
    const { data } = await api.delete(target)
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
  async getCodexStatus() {
    const { data } = await api.get("/settings/codex-status")
    return data
  },
  async codexLogin() {
    const { data } = await api.post("/settings/codex-login", {}, { timeout: 300000 })
    return data
  },
  async getUIPrefs() {
    const { data } = await api.get("/settings/ui-prefs")
    return data
  },
  async updateUIPrefs(values) {
    const { data } = await api.post("/settings/ui-prefs", { values })
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

/** Process-wide stats surface (Stats tab). */
export const statsAPI = {
  /** @returns {Promise<{count, total_bytes, oldest_at, newest_at, session_dir}>} */
  async diskUsage() {
    const { data } = await api.get("/sessions/disk-usage")
    return data
  },

  /** Aggregations over the cached session index — cheap, no rebuild. */
  async sessionStats() {
    const { data } = await api.get("/sessions/stats")
    return data
  },

  /**
   * Process-wide metrics snapshot — counters, sliding histograms, rate
   * buckets, gauges. Polled every 5 s by the Stats tab + the Dashboard
   * mini-strip. See ``api/routes/metrics.py`` for the shape contract.
   */
  async metrics() {
    const { data } = await api.get("/metrics/snapshot")
    return data
  },
}

/** Attach — informational policy hints consumed by Inspector Overview. */
export const attachAPI = {
  /** @returns {Promise<{policies: string[]}>} */
  async getCreaturePolicies(creatureId) {
    const { data } = await api.get(`/attach/policies/${encodeURIComponent(creatureId)}`)
    return data
  },

  /** @returns {Promise<{policies: string[]}>} */
  async getSessionPolicies(sessionId) {
    const { data } = await api.get(`/attach/session_policies/${encodeURIComponent(sessionId)}`)
    return data
  },
}

export default api
