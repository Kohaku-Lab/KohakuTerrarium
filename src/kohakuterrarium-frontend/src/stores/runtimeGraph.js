import { defineStore } from "pinia"
import { computed, reactive } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { runtimeGraphAPI, terrariumAPI, wiringAPI } from "@/utils/api"
import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"
import { wsUrl } from "@/utils/wsUrl"

import {
  FREE_STACK,
  GROUP_PADDING_BOTTOM,
  GROUP_PADDING_TOP,
  GROUP_PADDING_X,
  NODE_HEIGHT,
  NODE_WIDTH,
  Z_BANDS,
  normalizeSnapshot,
} from "@/stores/runtimeGraphModel"

export { FREE_STACK, NODE_HEIGHT, NODE_WIDTH, Z_BANDS }

const LAYOUT_KEY = "kt.runtimeGraph.layout.v1"

function defaultLayout() {
  return { nodes: {}, connections: {}, groups: {}, view: {} }
}

function readLayout() {
  return getHybridPrefSync(LAYOUT_KEY, defaultLayout(), { json: true }) || defaultLayout()
}

function writeLayout(layout) {
  setHybridPref(LAYOUT_KEY, layout || defaultLayout(), { json: true })
}

export const useRuntimeGraphStore = defineStore("runtimeGraph", () => {
  const state = reactive({
    loading: false,
    error: "",
    version: 0,
    rawSnapshot: null,
    nodes: [],
    groups: [],
    groupStack: [],
    connections: [],
    selection: { kind: null, id: null },
    hoveredConnectionId: null,
    expandedConnectionId: null,
    pendingDropTarget: null,
    transientLog: [],
    zoom: 1,
    panX: 0,
    panY: 0,
    wsConnected: false,
    wsError: "",
    // Free-floating channels — created in the UI but not yet bound to
    // any backend graph. They render as free channel cards and only
    // become real engine channels once a creature is wired to them.
    freeChannels: [],
    _layout: readLayout(),
    _pollInterval: null,
    _ws: null,
    _reloadTimer: null,
  })

  const nodeById = computed(() => Object.fromEntries(state.nodes.map((n) => [n.id, n])))
  const groupById = computed(() => Object.fromEntries(state.groups.map((g) => [g.id, g])))
  const nodesByGroup = computed(() => {
    const map = {}
    for (const group of state.groups) map[group.id] = []
    for (const node of state.nodes) {
      if (node.groupId && map[node.groupId]) map[node.groupId].push(node)
    }
    return map
  })

  function groupBounds(groupId) {
    const members = state.nodes.filter((n) => n.groupId === groupId)
    if (members.length === 0) return { x: 0, y: 0, width: 0, height: 0 }
    const x = Math.min(...members.map((n) => n.x)) - GROUP_PADDING_X
    const y = Math.min(...members.map((n) => n.y)) - GROUP_PADDING_TOP
    const right = Math.max(...members.map((n) => n.x + NODE_WIDTH)) + GROUP_PADDING_X
    const bottom = Math.max(...members.map((n) => n.y + NODE_HEIGHT)) + GROUP_PADDING_BOTTOM
    return { x, y, width: right - x, height: bottom - y }
  }

  function syncGroupStack(groups) {
    const ids = groups.map((g) => g.id)
    const known = new Set(ids)
    state.groupStack = state.groupStack.filter((id) => known.has(id))
    for (const id of ids) {
      if (!state.groupStack.includes(id)) state.groupStack.push(id)
    }
  }

  function bringStackToFront(stackId) {
    if (!stackId || stackId === FREE_STACK) return
    const idx = state.groupStack.indexOf(stackId)
    if (idx < 0) {
      state.groupStack.push(stackId)
      return
    }
    if (idx === state.groupStack.length - 1) return
    state.groupStack.splice(idx, 1)
    state.groupStack.push(stackId)
  }

  function stackZBase(stackId) {
    const id = stackId || FREE_STACK
    if (id === FREE_STACK) return Z_BANDS.freeBase
    const idx = state.groupStack.indexOf(id)
    return idx < 0 ? Z_BANDS.groupBase : Z_BANDS.groupBase + idx * Z_BANDS.bandWidth
  }

  async function loadSnapshot() {
    state.loading = true
    state.error = ""
    try {
      applySnapshot(await runtimeGraphAPI.snapshot())
    } catch (err) {
      state.error = err?.message || String(err)
    } finally {
      state.loading = false
    }
  }

  function applySnapshot(snapshot) {
    const layout = readLayout()
    const normalized = normalizeSnapshot(snapshot, layout)
    state._layout = layout
    state.rawSnapshot = snapshot
    state.version = snapshot?.version || Date.now()
    // Drop any free channel that has been promoted to a real engine
    // channel — match by name (the user-supplied identifier survives
    // the promotion because we pass it to the backend verbatim).
    if (state.freeChannels.length) {
      const realChannelNames = new Set(
        normalized.nodes.filter((n) => n.kind === "channel").map((n) => n.label),
      )
      state.freeChannels = state.freeChannels.filter((c) => !realChannelNames.has(c.label))
    }
    state.nodes = [...normalized.nodes, ...state.freeChannels]
    state.groups = normalized.groups
    state.connections = normalized.connections
    state.zoom = layout.view?.zoom ?? state.zoom
    state.panX = layout.view?.panX ?? state.panX
    state.panY = layout.view?.panY ?? state.panY
    syncGroupStack(normalized.groups)
  }

  function startPolling() {
    if (state._pollInterval) return
    // The websocket is the primary live channel; polling is only a
    // cold fallback when the WS is down. Keeping both running at the
    // same time is the source of the perceived flicker — every poll
    // replaces the entire `state.nodes` array which causes Vue to
    // re-render every card.
    state._pollInterval = createVisibilityInterval(() => {
      if (state.wsConnected) return
      loadSnapshot()
    }, 5000)
    state._pollInterval.start()
  }

  function stopPolling() {
    if (!state._pollInterval) return
    state._pollInterval.stop()
    state._pollInterval = null
  }

  function startLive() {
    if (state._ws || typeof WebSocket === "undefined") return
    try {
      const socket = new WebSocket(wsUrl("/ws/runtime/graph"))
      state._ws = socket
      socket.onopen = () => {
        state.wsConnected = true
        state.wsError = ""
      }
      socket.onmessage = (event) => {
        try {
          applyRuntimeEvent(JSON.parse(event.data))
        } catch {
          /* ignore malformed websocket frames */
        }
      }
      socket.onerror = () => {
        state.wsError = "Runtime graph websocket error"
      }
      socket.onclose = () => {
        state.wsConnected = false
        state._ws = null
      }
    } catch (err) {
      state.wsError = err?.message || String(err)
    }
  }

  function stopLive() {
    if (!state._ws) return
    const socket = state._ws
    state._ws = null
    socket.close()
    state.wsConnected = false
  }

  function applyRuntimeEvent(event) {
    if (!event) return
    if (event.type === "snapshot") {
      applySnapshot(event.snapshot)
      return
    }
    if (event.type === "channel_message") {
      patchChannelMessage(event)
      return
    }
    if (["topology_changed", "creature_started", "creature_stopped"].includes(event.type)) {
      scheduleReload()
    }
  }

  function patchChannelMessage(event) {
    const graphId = event.graph_id
    const channelName = event.channel
    const preview = event.content_preview || _localPreview(event.content)
    for (const connection of state.connections) {
      if (
        connection.backend?.kind === "channel_edge" &&
        connection.backend.graphId === graphId &&
        connection.backend.channelName === channelName
      ) {
        if (preview) connection.brief = preview
        if (event.sender) connection.details = `latest from ${event.sender}`
      }
    }
    // Don't push per-message log entries — channel chat can fire many
    // times per second and swamps the transient-log strip with chatter
    // that obscures actual topology actions.
  }

  function _localPreview(value) {
    if (value == null) return ""
    if (typeof value === "string") return value.slice(0, 160)
    try {
      return JSON.stringify(value).slice(0, 160)
    } catch {
      return String(value).slice(0, 160)
    }
  }

  function scheduleReload() {
    if (state._reloadTimer) clearTimeout(state._reloadTimer)
    state._reloadTimer = setTimeout(() => {
      state._reloadTimer = null
      loadSnapshot()
    }, 150)
  }

  function selectNode(id) {
    state.selection = { kind: "node", id }
    state.expandedConnectionId = null
    bringStackToFront(nodeById.value[id]?.groupId || FREE_STACK)
  }

  function selectGroup(id) {
    state.selection = { kind: "group", id }
    state.expandedConnectionId = null
    bringStackToFront(id)
  }

  function selectConnection(id) {
    state.selection = { kind: "connection", id }
    state.expandedConnectionId = id
    bringStackToFront(state.connections.find((c) => c.id === id)?.groupId || FREE_STACK)
  }

  function clearSelection() {
    state.selection = { kind: null, id: null }
    state.expandedConnectionId = null
  }

  function rememberNodePosition(node) {
    state._layout.nodes = state._layout.nodes || {}
    state._layout.nodes[node.id] = { x: node.x, y: node.y }
    writeLayout(state._layout)
  }

  function rememberConnectionRoute(connection) {
    state._layout.connections = state._layout.connections || {}
    state._layout.connections[connection.id] = { routeOffset: connection.routeOffset || 0 }
    writeLayout(state._layout)
  }

  function rememberGroup(group) {
    state._layout.groups = state._layout.groups || {}
    state._layout.groups[group.id] = { collapsed: group.collapsed === true }
    writeLayout(state._layout)
  }

  function rememberView() {
    state._layout.view = { zoom: state.zoom, panX: state.panX, panY: state.panY }
    writeLayout(state._layout)
  }

  // Hooks for the modal-based creation flows that already exist in the
  // v2 shell. The graph editor doesn't own the modals, so we expose a
  // registration API and the GraphEditorTab plugs them in on mount.
  const _createHooks = {
    creature: null,
    terrarium: null,
    channel: null,
  }
  function registerCreateHook(kind, fn) {
    _createHooks[kind] = fn || null
  }
  function addNode(kind, opts = {}) {
    const hook = _createHooks[kind]
    if (typeof hook === "function") {
      hook(opts)
      return null
    }
    pushLog(`add ${kind} · no creation flow registered`)
    return null
  }

  // Drop a free channel onto the canvas. It only becomes a real engine
  // channel once a creature is wired to it (see ``connectNodes``).
  function addFreeChannel({ name, x = 200, y = 200 }) {
    if (!name) return null
    const id = `free-channel:${name}:${Date.now()}`
    const node = {
      id,
      label: name,
      kind: "channel",
      status: "idle",
      groupId: null,
      graphId: null,
      x,
      y,
      backend: { kind: "free_channel", name },
    }
    state.freeChannels.push(node)
    state.nodes = [...state.nodes, node]
    pushLog(`free channel "${name}" — drag a creature handle to it to bind`)
    return node
  }

  function removeFreeChannel(id) {
    state.freeChannels = state.freeChannels.filter((c) => c.id !== id)
    state.nodes = state.nodes.filter((n) => n.id !== id)
  }

  function moveNode(id, dx, dy) {
    const node = nodeById.value[id]
    if (!node) return
    node.x += dx
    node.y += dy
    rememberNodePosition(node)
  }

  function moveGroup(groupId, dx, dy) {
    for (const node of state.nodes) {
      if (node.groupId !== groupId) continue
      node.x += dx
      node.y += dy
      rememberNodePosition(node)
    }
  }

  function setRouteOffset(id, routeOffset) {
    const connection = state.connections.find((c) => c.id === id)
    if (!connection) return
    connection.routeOffset = routeOffset
    rememberConnectionRoute(connection)
  }

  function zoomBy(factor, anchorX = null, anchorY = null) {
    const newZoom = Math.max(0.4, Math.min(1.8, state.zoom * factor))
    if (anchorX != null && anchorY != null) {
      const k = newZoom / state.zoom
      state.panX = anchorX - (anchorX - state.panX) * k
      state.panY = anchorY - (anchorY - state.panY) * k
    }
    state.zoom = newZoom
    rememberView()
  }

  function pan(dx, dy) {
    state.panX += dx
    state.panY += dy
    rememberView()
  }

  function resetView() {
    state.zoom = 1
    state.panX = 0
    state.panY = 0
    rememberView()
  }

  function toggleGroupCollapse(id) {
    const group = groupById.value[id]
    if (!group) return
    group.collapsed = !group.collapsed
    rememberGroup(group)
  }

  async function connect(sourceId, targetId) {
    const source = nodeById.value[sourceId]
    const target = nodeById.value[targetId]
    if (!source || !target || source.id === target.id) return null
    try {
      const result = await connectNodes(source, target)
      // Don't refetch the runtime-graph snapshot here — the backend
      // emits a ``topology_changed`` event and the websocket driver
      // will trigger a debounced reload via ``scheduleReload``.
      // We *do* nudge the instances rail though: a cross-graph wire
      // merges two sessions into one, and waiting up to 5s for the
      // next instances poll would leave the rail showing two rows
      // for a session that no longer exists. Single-flight on the
      // instances side keeps this from racing with that poll.
      _refreshInstancesSoon()
      return result
    } catch (err) {
      pushLog(`connect failed · ${err?.message || err}`)
      throw err
    }
  }

  // Lazy-imported to avoid a Pinia init cycle (instances store imports
  // pieces from `runtimeGraphModel`'s neighborhood at module load).
  function _refreshInstancesSoon() {
    import("@/stores/instances")
      .then(({ useInstancesStore }) => {
        useInstancesStore().fetchAll()
      })
      .catch(() => {
        /* instances store unavailable in tests — fine */
      })
  }

  async function connectNodes(source, target) {
    // creature → creature : direct output wiring. Cross-graph drags
    // are handled by the backend, which auto-merges the two graphs
    // before adding the wire (no channel side-effect). The molecules
    // collapse into one after the next snapshot reload.
    if (source.kind === "creature" && target.kind === "creature") {
      pushLog(
        source.graphId === target.graphId
          ? `wired ${source.label} → ${target.label}`
          : `merging molecules · wired ${source.label} → ${target.label}`,
      )
      return wiringAPI.addOutput(source.graphId, source.id, {
        to: target.id,
        with_content: true,
        prompt_format: "simple",
        allow_self_trigger: false,
      })
    }
    // creature → channel : channel send wiring. If the channel is a
    // free placeholder we materialise it inside the creature's graph
    // first; otherwise both must already share a backend graph.
    if (source.kind === "creature" && target.kind === "channel") {
      return await wireCreatureToChannel(source, target, "send")
    }
    // channel → creature : channel listen wiring (mirror).
    if (source.kind === "channel" && target.kind === "creature") {
      return await wireCreatureToChannel(target, source, "listen")
    }
    pushLog(
      "supported drags: creature→creature, creature↔channel (channel will bind to the creature's molecule)",
    )
    return null
  }

  // Common path for creature↔channel drags. ``direction`` is the
  // creature's role: "send" (creature publishes) or "listen" (creature
  // subscribes). For a free channel we first call ``addChannel`` on
  // the creature's graph so the channel comes into existence in that
  // engine namespace, then run the wiring call.
  async function wireCreatureToChannel(creature, channel, direction) {
    const channelName = channel.backend?.name || channel.label
    const isFree = channel.backend?.kind === "free_channel" || !channel.graphId
    let targetGraphId = creature.graphId
    if (isFree) {
      try {
        await terrariumAPI.addChannel(creature.graphId, channelName, "queue", "")
      } catch (err) {
        const status = err?.response?.status
        // 400 is "already exists" — fine, fall through to the wire.
        if (status !== 400) {
          pushLog(`bind channel failed · ${err?.message || err}`)
          throw err
        }
      }
      removeFreeChannel(channel.id)
      pushLog(`bound channel "${channelName}" to ${creature.label}'s molecule`)
    } else if (creature.graphId !== channel.graphId) {
      // The channel and the creature live in different graphs. Without
      // a merge, each side would see its OWN channel object with the
      // same name and messages would never cross. ensure_same_graph on
      // the backend folds them together (no bridge channel created),
      // then we wire on the surviving graph.
      try {
        const result = await terrariumAPI.mergeGraphs(channel.graphId, creature.graphId)
        targetGraphId = result?.session_id || channel.graphId
        pushLog(
          `merged ${creature.label}'s molecule into ${channelName}'s so they share the channel`,
        )
      } catch (err) {
        pushLog(`merge for channel wire failed · ${err?.message || err}`)
        throw err
      }
    } else {
      targetGraphId = channel.graphId
    }
    pushLog(
      direction === "send"
        ? `${creature.label} sends to ${channelName}`
        : `${creature.label} listens on ${channelName}`,
    )
    return terrariumAPI.wireCreature(targetGraphId, creature.id, channelName, direction)
  }

  async function deleteConnection(id) {
    const connection = state.connections.find((c) => c.id === id)
    if (!connection) return
    try {
      if (connection.backend?.kind === "channel_edge") {
        if (connection.aToB) await setChannelDirection(connection, "aToB", false)
        if (connection.bToA) await setChannelDirection(connection, "bToA", false)
      } else if (connection.backend?.kind === "output_edge") {
        const { graphId, forwardEdgeId, reverseEdgeId, a, b } = connection.backend
        if (forwardEdgeId) await wiringAPI.removeOutput(graphId, a, forwardEdgeId)
        if (reverseEdgeId) await wiringAPI.removeOutput(graphId, b, reverseEdgeId)
      }
      pushLog(`removed ${connection.label}`)
      // WS will push a topology_changed → snapshot reloads itself.
      _refreshInstancesSoon()
    } catch (err) {
      pushLog(`remove failed · ${err?.message || err}`)
      throw err
    }
  }

  async function toggleDirection(id, which) {
    const connection = state.connections.find((c) => c.id === id)
    if (!connection) return
    try {
      if (connection.backend?.kind === "output_edge") {
        await toggleOutputDirection(connection, which)
        return
      }
      if (connection.backend?.kind !== "channel_edge") return
      await setChannelDirection(connection, which, !connection[which])
      pushLog(`${connection.label} · ${which === "aToB" ? "send" : "listen"} updated`)
    } catch (err) {
      pushLog(`wire failed · ${err?.message || err}`)
      throw err
    }
  }

  async function setChannelDirection(connection, which, enabled) {
    const direction = which === "aToB" ? "send" : "listen"
    const apiCall = enabled ? terrariumAPI.wireCreature : terrariumAPI.unwireCreature
    return apiCall(
      connection.backend.graphId,
      connection.backend.creatureId,
      connection.backend.channelName,
      direction,
    )
  }

  // For an `output_edge` pair, `aToB` controls the `a→b` wire and
  // `bToA` controls the `b→a` wire. Each toggle adds or removes the
  // matching backend edge id; missing ids mean the edge isn't there
  // yet, so we add it.
  async function toggleOutputDirection(connection, which) {
    const { graphId, a, b, forwardEdgeId, reverseEdgeId } = connection.backend
    const isForward = which === "aToB"
    const enabled = !connection[which]
    const from = isForward ? a : b
    const to = isForward ? b : a
    const existingId = isForward ? forwardEdgeId : reverseEdgeId
    if (enabled) {
      await wiringAPI.addOutput(graphId, from, {
        to,
        with_content: true,
        prompt_format: "simple",
        allow_self_trigger: false,
      })
      pushLog(`wired ${from} → ${to}`)
    } else if (existingId) {
      await wiringAPI.removeOutput(graphId, from, existingId)
      pushLog(`unwired ${from} → ${to}`)
    }
  }

  async function joinGroup() {
    pushLog("graph merge happens automatically when wiring across molecules — drag a wire instead")
  }

  async function removeNodeFromGroup() {
    pushLog("splitting a creature out of its molecule requires removing every wire that ties it in")
  }

  async function dissolveGroup(groupId) {
    if (!groupId) return
    try {
      // Stopping the session is the engine-level equivalent of
      // dissolving the molecule — it tears down the graph and removes
      // every creature/channel inside it.
      await terrariumAPI.stop(groupId)
      pushLog(`stopped session ${groupId}`)
    } catch (err) {
      pushLog(`stop failed · ${err?.message || err}`)
    }
  }

  function pushLog(msg) {
    state.transientLog.unshift({ id: Date.now() + Math.random(), msg, ts: Date.now() })
    if (state.transientLog.length > 12) state.transientLog.length = 12
  }

  return {
    state,
    nodeById,
    groupById,
    nodesByGroup,
    groupBounds,
    bringStackToFront,
    stackZBase,
    loadSnapshot,
    applySnapshot,
    startPolling,
    stopPolling,
    startLive,
    stopLive,
    applyRuntimeEvent,
    selectNode,
    selectGroup,
    selectConnection,
    clearSelection,
    addNode,
    addFreeChannel,
    removeFreeChannel,
    registerCreateHook,
    moveNode,
    moveGroup,
    setRouteOffset,
    removeNodeFromGroup,
    joinGroup,
    dissolveGroup,
    connect,
    toggleDirection,
    deleteConnection,
    zoomBy,
    pan,
    resetView,
    toggleGroupCollapse,
    pushLog,
  }
})
