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

  /**
   * Fetch server runtime info. When ``opts.onNode`` is a connected worker
   * (i.e. not ``"_host"``), the request includes ``?on_node=<node>`` so the
   * backend returns that worker's default working directory instead of the
   * host's cwd (B5). Standalone mode keeps the original behavior.
   *
   * @param {{ onNode?: string }} [opts]
   * @returns {Promise<{cwd: string, platform: string}>}
   */
  async getServerInfo(opts = {}) {
    const params = {}
    if (opts.onNode && opts.onNode !== "_host") params.on_node = opts.onNode
    const { data } = await api.get("/configs/server-info", { params })
    return data
  },

  /** Full diagnostic snapshot for the About panel. */
  async getDiagnostics() {
    const { data } = await api.get("/configs/server-info/diagnostics")
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
  async create(configPath, pwd, name = null, opts = {}) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    if (name) body.name = name
    // Lab cluster site — backend defaults to "_host" if absent, so
    // standalone mode is unaffected.
    if (opts.onNode && opts.onNode !== "_host") body.on_node = opts.onNode
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
   * ``session_id`` is the surviving graph id.
   *
   * ``channel`` (optional): when set, the backend's underlying
   * ``service.connect`` reuses that channel name instead of creating
   * a fresh auto-named ``{a}_to_{b}`` bridge.  Pass this when the
   * user dragged FROM an existing channel — otherwise the merge would
   * spawn a parallel channel alongside the user's, which is the
   * wrong UX. */
  async mergeGraphs(aSessionId, bSessionId, channel = null) {
    const url =
      `/sessions/topology/${encodeTarget(aSessionId)}/merge/${encodeTarget(bSessionId)}` +
      (channel ? `?channel=${encodeURIComponent(channel)}` : "")
    const { data } = await api.post(url)
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

  async getWorkingDir(id, target) {
    const { data } = await api.get(`/sessions/${id}/creatures/${encodeTarget(target)}/working-dir`)
    return data
  },

  async setWorkingDir(id, target, path) {
    const { data } = await api.put(
      `/sessions/${id}/creatures/${encodeTarget(target)}/working-dir`,
      { path },
    )
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
  async create(configPath, pwd, name = null, opts = {}) {
    const body = { config_path: configPath }
    if (pwd) body.pwd = pwd
    if (name) body.name = name
    if (opts.onNode && opts.onNode !== "_host") body.on_node = opts.onNode
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

  /** Regenerate the last assistant response.
   *
   * ``sessionId`` is the terrarium's session id (or ``"_"`` for a
   * standalone agent). ``creatureId`` is the target creature/agent
   * name. Old call sites that pass only an agent id can still call
   * ``regenerate(agentId)`` — the second arg defaults to the first.
   */
  async regenerate(sessionId, creatureId, { turnIndex, branchView } = {}) {
    const sid = sessionId || "_"
    const cid = creatureId || sessionId
    const body = {}
    if (turnIndex != null) body.turn_index = turnIndex
    if (branchView && Object.keys(branchView).length) body.branch_view = branchView
    const { data } = await api.post(
      `/sessions/${encodeTarget(sid)}/creatures/${encodeTarget(cid)}/regenerate`,
      body,
    )
    return data
  },

  /** Edit a user message at a given index and re-run */
  async editMessage(sessionId, creatureId, msgIdx, content, target = {}) {
    const body = { content }
    if (target.turnIndex != null) body.turn_index = target.turnIndex
    if (target.userPosition != null) body.user_position = target.userPosition
    if (target.branchView && Object.keys(target.branchView).length) {
      body.branch_view = target.branchView
    }
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

  async getTree(root, depth = 1) {
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
  async resume(sessionName, opts = {}) {
    const body = {}
    if (opts.onNode && opts.onNode !== "_host") body.on_node = opts.onNode
    const { data } = await api.post(`/sessions/${sessionName}/resume`, body)
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

  /**
   * Get the vector-index status for a saved session.
   * @param {string} sessionName
   * @returns {Promise<{indexed: boolean, embedder: string|null, model: string|null,
   *                    dimensions: number|null, fts_blocks: number, vec_blocks: number,
   *                    agents: string[]}>}
   */
  async getMemoryStatus(sessionName) {
    const { data } = await api.get(`/sessions/${sessionName}/memory/status`)
    return data
  },

  /**
   * Acknowledge a build request and return the WS URL for progress.
   * The actual work runs on the WS stream returned by ``openMemoryBuildStream``.
   * @param {string} sessionName
   * @param {{embedder?: string, model?: string|null, dimensions?: number|null, force?: boolean}} body
   */
  async buildMemory(sessionName, body = {}) {
    const payload = {
      embedder: body.embedder || "auto",
      model: body.model || null,
      dimensions: body.dimensions || null,
      force: !!body.force,
    }
    const { data } = await api.post(`/sessions/${sessionName}/memory/build`, payload)
    return data
  },

  /**
   * Open the WS that streams memory-build progress. Caller is
   * responsible for closing the socket; the server closes after the
   * terminal ``{status: ok|failed|cancelled}`` frame.
   * @param {string} sessionName
   * @param {{embedder?: string, model?: string|null, dimensions?: number|null,
   *          force?: boolean, onFrame: (frame: object) => void,
   *          onClose?: () => void, onError?: (e: Event) => void}} opts
   * @returns {WebSocket}
   */
  openMemoryBuildStream(sessionName, opts) {
    const params = new URLSearchParams()
    if (opts.embedder) params.set("embedder", opts.embedder)
    if (opts.model) params.set("model", opts.model)
    if (opts.dimensions) params.set("dimensions", String(opts.dimensions))
    if (opts.force) params.set("force", "true")
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    const url = `${proto}//${window.location.host}/ws/sessions/${encodeURIComponent(sessionName)}/memory/build?${params.toString()}`
    const ws = new WebSocket(url)
    ws.onmessage = (e) => {
      try {
        opts.onFrame(JSON.parse(e.data))
      } catch (err) {
        opts.onError?.(err)
      }
    }
    ws.onerror = (e) => opts.onError?.(e)
    ws.onclose = () => opts.onClose?.()
    return ws
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

/** Settings - API keys, custom models.
 *
 * Identity ops (keys, codex) accept an optional ``node`` argument that
 * routes the call to a specific worker's local credential store (see
 * src/kohakuterrarium/api/routes/identity/node_routing.py). Omit or
 * pass "_host" to hit the host's own store (the default standalone
 * behaviour). */
const _nodeQuery = (node) => (node && node !== "_host" ? { params: { node } } : undefined)
export const settingsAPI = {
  async getKeys(node = "_host") {
    const { data } = await api.get("/settings/keys", _nodeQuery(node))
    return data
  },
  async saveKey(provider, key, node = "_host") {
    const cfg = _nodeQuery(node) || {}
    const { data } = await api.post("/settings/keys", { provider, key }, cfg)
    return data
  },
  async removeKey(provider, node = "_host") {
    const { data } = await api.delete(`/settings/keys/${provider}`, _nodeQuery(node))
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
  // Raw config files (Settings → Advanced)
  async listConfigFiles() {
    const { data } = await api.get("/settings/config-files")
    return data
  },
  async readConfigFile(name) {
    const { data } = await api.get(`/settings/config-files/${encodeURIComponent(name)}/content`)
    return data
  },
  async writeConfigFile(name, content, sha256Expected = null) {
    const body = { content }
    if (sha256Expected) body.sha256_expected = sha256Expected
    const { data } = await api.put(
      `/settings/config-files/${encodeURIComponent(name)}/content`,
      body,
    )
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
  /**
   * Partial in-place edit of an existing MCP server.
   * Send only the fields you want to change.
   * @param {string} name
   * @param {object} patch
   */
  async patchMCP(name, patch) {
    const { data } = await api.patch(`/settings/mcp/${name}`, patch)
    return data
  },
  /**
   * Probe an MCP server: connect, list_tools, disconnect.
   * @returns {Promise<{ok: boolean, error: string|null, tool_count: number|null, elapsed_ms: number|null}>}
   */
  async testMCP(name) {
    const { data } = await api.post(`/settings/mcp/${name}/test`)
    return data
  },
  /**
   * List installed creatures / terrariums that reference this server.
   * @returns {Promise<{name: string, kind: 'creature'|'terrarium', path: string}[]>}
   */
  async mcpUsage(name) {
    const { data } = await api.get(`/settings/mcp/${name}/usage`)
    return data
  },
  async getCodexUsage() {
    const { data } = await api.get("/settings/codex-usage")
    return data
  },
  async getCodexStatus(node = "_host") {
    const { data } = await api.get("/settings/codex-status", _nodeQuery(node))
    return data
  },
  async codexLogin(node = "_host") {
    const cfg = { timeout: 300000, ..._nodeQuery(node) }
    const { data } = await api.post("/settings/codex-login", {}, cfg)
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
  /** Update a single git-backed installed package. */
  async update(name) {
    const { data } = await api.post(`/registry/${encodeURIComponent(name)}/update`)
    return data
  },
  /** Aggregated list of plugin / tool / trigger / etc. extensions
   *  contributed by installed packages.
   *  @returns {Promise<{name, kind, package, package_version, description, module, editable}[]>}
   */
  // (also exposed as extensionsAPI.list — kept here for the
  // packages-tab cross-reference; UI components use extensionsAPI.)
  async listExtensions() {
    const { data } = await api.get("/registry/extensions")
    return data
  },
  /** Update every git-backed installed package. */
  async updateAll() {
    const { data } = await api.post("/registry/update-all")
    return data
  },
  /** List files inside an installed package. */
  async listFiles(name) {
    const { data } = await api.get(`/registry/${encodeURIComponent(name)}/files`)
    return data
  },
  /** Read one file from an installed package as UTF-8 text. */
  async readFile(name, path) {
    const { data } = await api.get(
      `/registry/${encodeURIComponent(name)}/files/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    )
    return data
  },
  /** Write one file inside an installed package. */
  async writeFile(name, path, content, sha256Expected = null) {
    const body = { content }
    if (sha256Expected) body.sha256_expected = sha256Expected
    const { data } = await api.put(
      `/registry/${encodeURIComponent(name)}/files/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      body,
    )
    return data
  },
}

/** Lab cluster control — Sites tab verbs (lab-host mode only). */
export const labAPI = {
  async status() {
    const { data } = await api.get("/lab/status")
    return data
  },
  async disconnectClient(nodeId) {
    const { data } = await api.post(`/lab/clients/${encodeURIComponent(nodeId)}/disconnect`)
    return data
  },
  async blockClient(nodeId, reason = "") {
    const { data } = await api.post(`/lab/clients/${encodeURIComponent(nodeId)}/block`, { reason })
    return data
  },
  async unblockClient(nodeId) {
    const { data } = await api.delete(`/lab/clients/blocklist/${encodeURIComponent(nodeId)}`)
    return data
  },
  async listBlocked() {
    const { data } = await api.get("/lab/clients/blocklist")
    return data
  },
  async rotatePairingToken() {
    const { data } = await api.post("/lab/pairing-tokens/rotate")
    return data
  },
}

/** Extensions catalog — flattened view of plugins / tools / triggers /
 *  io / llm-presets / skills / commands / prompts contributed by
 *  installed packages.
 */
export const extensionsAPI = {
  async list() {
    const { data } = await api.get("/registry/extensions")
    return data
  },
  async get(kind, name) {
    const { data } = await api.get(
      `/registry/extensions/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`,
    )
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

/**
 * Cluster (lab-host) nodes API.
 *
 * The lab-host mode exposes a list of connected sites (host + workers).
 * In standalone mode every endpoint returns 404 — callers must catch.
 * See ``api/routes/nodes.py`` for the backend.
 *
 * Wire field is ``node_id`` (immutable contract).  Frontend code uses
 * ``siteId`` to avoid confusion with graph-node terminology — see
 * planned-frontend-modification.md §0.
 */
export const nodesAPI = {
  /**
   * GET /api/nodes
   * @returns {Promise<{nodes: Array<{node_id: string, is_host: boolean, status: string, creatures: number|null}>}>}
   */
  async list() {
    const { data } = await api.get("/nodes")
    return data
  },

  /**
   * GET /api/nodes/:node_id/status
   * @param {string} nodeId
   * @returns {Promise<{node_id: string, is_host: boolean, ok: boolean, creatures: number, status_snapshot: object}>}
   */
  async status(nodeId) {
    const { data } = await api.get(`/nodes/${encodeURIComponent(nodeId)}/status`)
    return data
  },

  /**
   * POST /api/nodes/:node_id/deploy/creature
   * @param {string} nodeId
   * @param {string} workspacePath  Local absolute path to a creature directory.
   * @returns {Promise<{target_path: string, node_id: string}>}
   */
  async deployCreature(nodeId, workspacePath) {
    const { data } = await api.post(`/nodes/${encodeURIComponent(nodeId)}/deploy/creature`, {
      workspace_path: workspacePath,
    })
    return data
  },
}

export default api
