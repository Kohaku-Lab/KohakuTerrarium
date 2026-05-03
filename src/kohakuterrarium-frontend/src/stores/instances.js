import { terrariumAPI, agentAPI } from "@/utils/api"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"

export const useInstancesStore = defineStore("instances", {
  state: () => ({
    /** @type {import('@/utils/api').InstanceInfo[]} */
    list: [],
    /** @type {import('@/utils/api').InstanceInfo | null} */
    current: null,
    loading: false,
    /** @type {ReturnType<typeof createVisibilityInterval> | null} */
    _pollInterval: null,
    /** @type {number} Number of active subscribers (components using this store) */
    _subscribers: 0,
  }),

  getters: {
    running: (state) => state.list.filter((i) => i.status === "running"),
    terrariums: (state) => state.list.filter((i) => i.type === "terrarium"),
    creatures: (state) => state.list.filter((i) => i.type === "creature"),
  },

  actions: {
    /** Fetch all running instances (both terrariums and standalone agents) */
    async fetchAll() {
      this.loading = true
      try {
        const [terrariums, agents] = await Promise.all([terrariumAPI.list(), agentAPI.list()])

        const tInstances = terrariums.map((t) => _mapTerrarium(t))
        const aInstances = agents.map((a) => _mapAgent(a))
        this.list = [...tInstances, ...aInstances]
      } catch (err) {
        console.error("Failed to fetch instances:", err)
      } finally {
        this.loading = false
      }
    },

    /** Fetch a single instance by ID.
     *
     * The engine produces non-prefixed IDs (creature ``general_abc123``,
     * graph ``graph_def456``), so we can no longer dispatch by string
     * prefix. We instead resolve the type from ``this.list`` (populated
     * by ``fetchAll`` — including the post-create refresh in
     * :func:`create` below). For brand-new IDs not yet in the list we
     * try the terrarium endpoint first and fall back to the agent
     * endpoint on 404.
     */
    async fetchOne(id) {
      this.loading = true
      try {
        const existing = this.list.find((i) => i.id === id)
        let loaded = null
        if (existing?.type === "terrarium") {
          loaded = _mapTerrarium(await terrariumAPI.get(id))
        } else if (existing?.type === "creature") {
          loaded = _mapAgent(await agentAPI.get(id))
        } else {
          // Unknown type — probe both endpoints. Either succeeds quickly
          // (the active-session count is small) and 404 is the only
          // miss we expect when probing the wrong family.
          try {
            loaded = _mapTerrarium(await terrariumAPI.get(id))
          } catch (err) {
            if (err?.response?.status !== 404) throw err
            loaded = _mapAgent(await agentAPI.get(id))
          }
        }
        this.current = loaded
        const idx = this.list.findIndex((item) => item.id === loaded.id)
        if (idx >= 0) this.list.splice(idx, 1, loaded)
        else this.list.unshift(loaded)
        return loaded
      } catch (err) {
        if (err?.response?.status === 404) {
          this.list = this.list.filter((i) => i.id !== id)
          if (this.current?.id === id) this.current = null
          return null
        }
        console.error("Failed to fetch instance:", err)
        throw err
      } finally {
        this.loading = false
      }
    },

    /** Create a new instance */
    async create(type, configPath, pwd, name = null) {
      if (type === "terrarium") {
        const { terrarium_id } = await terrariumAPI.create(configPath, pwd, name)
        await this.fetchAll()
        return terrarium_id
      } else {
        const { agent_id } = await agentAPI.create(configPath, pwd, name)
        await this.fetchAll()
        return agent_id
      }
    },

    /** Stop an instance — awaits API response before removing from list */
    async stop(id) {
      try {
        const existing = this.list.find((i) => i.id === id)
        if (existing?.type === "terrarium") {
          await terrariumAPI.stop(id)
        } else if (existing?.type === "creature") {
          await agentAPI.stop(id)
        } else {
          // Unknown — probe both. Whichever isn't 404 will succeed.
          try {
            await terrariumAPI.stop(id)
          } catch (err) {
            if (err?.response?.status !== 404) throw err
            await agentAPI.stop(id)
          }
        }
        // Only remove after successful API response
        this.list = this.list.filter((i) => i.id !== id)
        if (this.current?.id === id) {
          this.current = null
        }
      } catch (err) {
        console.error("Failed to stop instance:", err)
        throw err
      }
    },

    /** Start auto-refresh polling. Called when a component mounts.
     *
     * Uses a visibility-aware interval so the store stops polling when
     * the tab is hidden — otherwise every running-instance row drives
     * a reactive update every 5 s even while the user isn't looking.
     */
    startPolling() {
      this._subscribers++
      if (this._pollInterval === null) {
        this._pollInterval = createVisibilityInterval(() => {
          this.fetchAll()
        }, 5000)
        this._pollInterval.start()
      }
    },

    /** Stop auto-refresh polling. Called when a component unmounts. */
    stopPolling() {
      this._subscribers = Math.max(0, this._subscribers - 1)
      if (this._subscribers === 0 && this._pollInterval !== null) {
        this._pollInterval.stop()
        this._pollInterval = null
      }
    },
  },
})

/** Map terrarium API response to frontend InstanceInfo */
function _mapTerrarium(data) {
  return {
    id: data.terrarium_id,
    type: "terrarium",
    config_name: data.name,
    pwd: data.pwd || "",
    status: data.running ? "running" : "stopped",
    has_root: !!data.has_root,
    model: data.root_model || "",
    llm_name: data.root_llm_name || "",
    provider: "",
    session_id: data.root_session_id || "",
    max_context: data.root_max_context || 0,
    compact_threshold: data.root_compact_threshold || 0,
    creatures: Object.entries(data.creatures || {}).map(([name, info]) => ({
      name,
      status: info.running ? "running" : "idle",
      model: info.model || "",
      llm_name: info.llm_name || "",
      listen_channels: info.listen_channels || [],
      send_channels: info.send_channels || [],
    })),
    channels: (data.channels || []).map((ch) => ({
      name: ch.name,
      type: ch.type,
      description: ch.description || "",
      message_count: ch.qsize || 0,
    })),
  }
}

/** Map agent API response to frontend InstanceInfo */
function _mapAgent(data) {
  return {
    id: data.agent_id,
    type: "creature",
    config_name: data.name || "agent",
    model: data.model || "",
    // Canonical ``provider/name[@variations]`` identifier used by the
    // ModelSwitcher pill so it keeps showing the full form after a
    // page refresh (per-session WS events aren't replayed).
    llm_name: data.llm_name || "",
    provider: data.provider || "",
    session_id: data.session_id || "",
    pwd: data.pwd || "",
    max_context: data.max_context || 0,
    compact_threshold: data.compact_threshold || 0,
    status: data.running ? "running" : "stopped",
    has_root: false,
    creatures: [
      {
        name: data.name || "agent",
        status: data.running ? "running" : "idle",
        model: data.model || "",
        llm_name: data.llm_name || "",
        listen_channels: [],
        send_channels: [],
      },
    ],
    channels: [],
  }
}
