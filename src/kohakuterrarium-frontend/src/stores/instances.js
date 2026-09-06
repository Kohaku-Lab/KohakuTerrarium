import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { agentAPI, sessionAPI, terrariumAPI } from "@/utils/api"

/**
 * Instances store — frontend's mirror of the engine's live sessions.
 *
 * One *session* per engine *graph*, regardless of how many creatures
 * live in it. Solo creatures and recipe-loaded terrariums share the
 * same shape; ``instance.creatures.length`` is the only "what does
 * this look like" signal panels need.
 *
 * ``instance.id`` always equals the canonical session_id (graph_id).
 * The frontend never needs to track creature_id-vs-graph_id divergence
 * — there is no divergence at the runtime layer.
 */
export const useInstancesStore = defineStore("instances", {
  state: () => ({
    /** @type {object[]} */
    list: [],
    /** @type {object | null} */
    current: null,
    loading: false,
    /** @type {ReturnType<typeof createVisibilityInterval> | null} */
    _pollInterval: null,
    _subscribers: 0,
    _inflightFetch: null,
    /** id -> shared in-flight fetchOne promise. */
    _inflightOne: {},
    _fetchSeq: 0,
  }),

  getters: {
    running: (state) => state.list.filter((i) => i.status === "running"),
    /** Sessions whose graph holds 2+ creatures (or recipe-loaded). */
    multiCreature: (state) => state.list.filter((i) => (i.creatures?.length || 0) > 1),
    /** Solo sessions — exactly one creature in the graph. */
    soloCreature: (state) => state.list.filter((i) => (i.creatures?.length || 0) <= 1),
  },

  actions: {
    async fetchAll() {
      if (this._inflightFetch) return this._inflightFetch
      this.loading = true
      const seq = ++this._fetchSeq
      let task
      task = (async () => {
        try {
          const sessions = await sessionAPI.listActive()
          if (seq !== this._fetchSeq) return
          this.list = sessions.map(_mapSession)
        } catch (err) {
          console.error("Failed to fetch instances:", err)
        } finally {
          if (this._inflightFetch === task) {
            this.loading = false
            this._inflightFetch = null
          }
        }
      })()
      this._inflightFetch = task
      return task
    },

    async fetchOne(id) {
      // Route navigation and the 5 s attach-tab poll can request the
      // same instance while a slow lookup is unanswered; share one
      // request instead of stacking duplicates. fetchAll already has
      // the equivalent single-request guard. Sharers inherit the first
      // caller's result or rejection — an error here is shared by
      // everyone joined on the request (every call site catches).
      const existing = this._inflightOne[id]
      if (existing) return existing
      const task = this._fetchOneNow(id).finally(() => {
        delete this._inflightOne[id]
      })
      this._inflightOne[id] = task
      return task
    },

    async _fetchOneNow(id) {
      this.loading = true
      const seq = this._fetchSeq
      try {
        const data = await sessionAPI.getActive(id)
        const loaded = _mapSession(data)
        if (seq !== this._fetchSeq) {
          return (
            this.list.find((item) => item.id === loaded.id) ??
            (this.current?.id === loaded.id ? this.current : null)
          )
        }
        this.current = loaded
        const idx = this.list.findIndex((item) => item.id === loaded.id)
        if (idx >= 0) {
          this.list.splice(idx, 1, loaded)
        } else {
          this.list.unshift(loaded)
        }
        return loaded
      } catch (err) {
        if (err?.response?.status === 404 && seq === this._fetchSeq) {
          this.list = this.list.filter((i) => i.id !== id)
          if (this.current?.id === id) this.current = null
          return null
        }
        if (err?.response?.status === 404) return null
        console.error("Failed to fetch instance:", err)
        throw err
      } finally {
        this.loading = false
      }
    },

    /** Create a new session.
     *
     * ``mode`` is the creation flavor — ``"creature"`` mints a 1-creature
     * graph from a creature config, ``"terrarium"`` applies a recipe.
     * Both produce the same Session shape and end up in the same list.
     */
    async create(mode, configPath, pwd, name = null, opts = {}) {
      const { onNode = "_host" } = opts
      if (mode === "terrarium") {
        const { terrarium_id } = await terrariumAPI.create(configPath, pwd, name, { onNode })
        await this.fetchAll()
        return terrarium_id
      }
      const { agent_id, session_id } = await agentAPI.create(configPath, pwd, name, { onNode })
      await this.fetchAll()
      // Prefer the canonical session_id when the backend surfaces it
      // (newer paths do), otherwise fall back to the historical
      // agent_id key — both resolve to the same session via
      // ``sessionAPI.getActive`` so the caller can route from either.
      return session_id || agent_id
    },

    markRuntimeStopped(id) {
      ++this._fetchSeq
      this._inflightFetch = null
      this.loading = false
      this.list = this.list.filter((i) => i.id !== id)
      if (this.current?.id === id) this.current = null
    },

    startPolling() {
      this._subscribers++
      if (this._pollInterval === null) {
        this._pollInterval = createVisibilityInterval(() => this.fetchAll(), 5000)
        this._pollInterval.start()
      }
    },

    stopPolling() {
      this._subscribers = Math.max(0, this._subscribers - 1)
      if (this._subscribers === 0 && this._pollInterval !== null) {
        this._pollInterval.stop()
        this._pollInterval = null
      }
    },
  },
})

/** Map a unified Session payload to the frontend InstanceInfo shape.
 *
 * Single mapper. The wire shape comes from
 * ``GET /api/sessions/active`` and ``GET /api/sessions/active/{id}``,
 * both of which return ``Session.to_dict()`` (session_id, name,
 * creatures, channels, …).
 *
 * For backward compat with frontend code that read ``instance.type``,
 * we still derive ``type`` from creature count: ``terrarium`` when 2+,
 * ``creature`` when ≤1. New code should prefer
 * ``creatures.length > 1`` directly.
 */
function _mapSession(data) {
  // ``GET /api/sessions/active`` returns ``SessionListing.to_dict()``
  // whose ``creatures`` is an INT count and whose home-site lives on
  // ``node_id``.  ``GET /api/sessions/active/{id}`` returns the full
  // ``Session.to_dict()`` with ``creatures: [...]`` + ``home_node``.
  // Coerce both shapes here so callers stay uniform.
  const sessionHome = data.home_node || data.node_id || "_host"
  const rawCreatures = Array.isArray(data.creatures) ? data.creatures : []
  const creatures = rawCreatures.map((c) => ({
    name: c.name || c.creature_id || "",
    config_name: c.config_name || "",
    config_ref: c.config_ref || "",
    creature_id: c.creature_id || c.agent_id || "",
    status: c.running ? "running" : "idle",
    model: c.model || "",
    llm_name: c.llm_name || "",
    max_context: c.max_context || 0,
    compact_threshold: c.compact_threshold || 0,
    listen_channels: c.listen_channels || [],
    send_channels: c.send_channels || [],
    is_root: !!c.is_root,
    // Per-creature lab cluster site; fall back to session-level home.
    home_node: c.home_node || sessionHome,
  }))
  const channels = (data.channels || []).map((ch) => ({
    name: ch.name,
    type: ch.type || "broadcast",
    description: ch.description || "",
    message_count: ch.qsize || ch.message_count || 0,
  }))
  // Pick the "primary" creature for legacy single-target panels:
  // root if recipe-flagged, else first creature.
  const primary = (data.has_root && creatures.find((c) => c.is_root)) || creatures[0] || {}
  // ``isMulti`` from creature array length OR from the int count on
  // the listing payload — same semantics, different wire shape.
  const creatureCount =
    creatures.length || (typeof data.creatures === "number" ? data.creatures : 0)
  const isMulti = creatureCount > 1
  return {
    id: data.session_id,
    graph_id: data.session_id,
    session_id: data.session_id,
    type: isMulti ? "terrarium" : "creature",
    session_name: data.name || "session",
    // Keep the legacy display field stable; configuration provenance is separate.
    config_name: data.name || primary.name || "session",
    creature_config_name: primary.config_name || "",
    config_ref: primary.config_ref || "",
    pwd: data.pwd || "",
    status: "running",
    has_root: !!data.has_root,
    config_path: data.config_path || "",
    created_at: data.created_at || "",
    model: primary.model || "",
    llm_name: primary.llm_name || "",
    provider: "",
    // Surface the primary creature's context limits so the
    // status-dashboard pbar (gated on ``maxContext > 0``) renders
    // for solo creatures and root-flagged terrariums alike.
    max_context: primary.max_context || 0,
    compact_threshold: primary.compact_threshold || 0,
    // Session-level home site for lab-host UI surfaces.
    home_node: sessionHome,
    creatures,
    channels,
  }
}
