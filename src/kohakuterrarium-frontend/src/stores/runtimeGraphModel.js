export const FREE_STACK = "__free"
export const NODE_WIDTH = 148
export const NODE_HEIGHT = 64
export const GROUP_PADDING_TOP = 56
export const GROUP_PADDING_X = 36
export const GROUP_PADDING_BOTTOM = 36
export const Z_BANDS = {
  groupBase: 100,
  bandWidth: 10,
  freeBase: 1000,
  membrane: 0,
  connection: 2,
  connectionExpanded: 6,
  node: 4,
  nodeSelected: 7,
  decoration: 9,
  pendingWire: 9000,
}

function channelNodeId(graphId, channelName) {
  return `channel:${graphId}:${channelName}`
}

function channelEdgeId(graphId, creatureId, channelName) {
  return `channel-edge:${graphId}:${creatureId}:${channelName}`
}

function outputPairId(graphId, a, b) {
  // Stable id per unordered pair so A↔B always lands on the same
  // connection regardless of which side the forward edge points from.
  const [low, high] = [a, b].sort()
  return `output-pair:${graphId}:${low}:${high}`
}

function normalizeCreatureStatus(creature) {
  if (creature.running === false) return "idle"
  if (creature.is_processing) return "running"
  return "waiting"
}

function normalizeChannelStatus(channel) {
  return Number(channel.qsize || 0) > 0 ? "running" : "idle"
}

function stableSortById(items) {
  return [...items].sort((a, b) => String(a.id).localeCompare(String(b.id)))
}

function autoLayoutNode(graphIndex, itemIndex, kind) {
  const col = itemIndex % 4
  const row = Math.floor(itemIndex / 4)
  const baseX = 120 + graphIndex * 520
  const baseY = 120 + row * 150
  return { x: baseX + col * 190, y: baseY + (kind === "channel" ? 72 : 0) }
}

function relationBriefForChannel(channel, creature, sends, listens) {
  if (channel.last_message?.content_preview) return channel.last_message.content_preview
  const modes = []
  if (sends) modes.push("send")
  if (listens) modes.push("recv")
  return `${creature.name || creature.creature_id} · ${modes.join(" · ") || channel.type || "channel"}`
}

function relationDetailsForChannel(channel) {
  const parts = []
  if (channel.description) parts.push(channel.description)
  if (channel.type) parts.push(`type: ${channel.type}`)
  if (channel.message_count != null) parts.push(`messages: ${channel.message_count}`)
  return parts.join(" · ")
}

function addCreatureNode(nodes, creature, graphId, hasMembrane, graphIndex, itemIndex, layout) {
  const id = creature.creature_id || creature.agent_id
  if (!id) return false
  const pos = layout.nodes?.[id] || autoLayoutNode(graphIndex, itemIndex, "creature")
  nodes.push({
    id,
    label: creature.name || id,
    kind: "creature",
    status: normalizeCreatureStatus(creature),
    // ``graphId`` is the backend graph this creature belongs to and is
    // what we send to the wiring API. ``groupId`` only drives the UI
    // membrane — null when the graph is a solo creature with no
    // channels, so the card renders as a free node instead of a
    // single-member molecule.
    graphId,
    groupId: hasMembrane ? graphId : null,
    x: pos.x,
    y: pos.y,
    backend: creature,
  })
  return true
}

function addChannelNode(nodes, channel, graphId, graphIndex, itemIndex, layout) {
  const id = channelNodeId(graphId, channel.name)
  const pos = layout.nodes?.[id] || autoLayoutNode(graphIndex, itemIndex, "channel")
  nodes.push({
    id,
    label: channel.name,
    kind: "channel",
    status: normalizeChannelStatus(channel),
    // Channels always sit inside a membrane — by definition the graph
    // that owns them is multi-tenant (or about to be).
    graphId,
    groupId: graphId,
    x: pos.x,
    y: pos.y,
    backend: channel,
  })
}

function addChannelEdges(connections, graph, layout) {
  const graphId = graph.graph_id
  const channels = graph.channels || []
  const channelByName = Object.fromEntries(channels.map((ch) => [ch.name, ch]))
  for (const creature of graph.creatures || []) {
    const creatureId = creature.creature_id || creature.agent_id
    if (!creatureId) continue
    const channelNames = new Set([
      ...(creature.send_channels || []),
      ...(creature.listen_channels || []),
    ])
    for (const channelName of channelNames) {
      const channel = channelByName[channelName] || { name: channelName, type: "queue" }
      const sends = (creature.send_channels || []).includes(channelName)
      const listens = (creature.listen_channels || []).includes(channelName)
      const id = channelEdgeId(graphId, creatureId, channelName)
      connections.push({
        id,
        a: creatureId,
        b: channelNodeId(graphId, channelName),
        groupId: graphId,
        label: channelName,
        brief: relationBriefForChannel(channel, creature, sends, listens),
        details: relationDetailsForChannel(channel),
        routeOffset: layout.connections?.[id]?.routeOffset || 0,
        aToB: sends,
        bToA: listens,
        backend: { kind: "channel_edge", graphId, creatureId, channelName },
      })
    }
  }
}

function addOutputEdges(connections, graph, layout) {
  const graphId = graph.graph_id
  const edges = (graph.output_edges || []).filter(
    (e) => e.from && (e.to_creature_id || e.to) && (e.edge_id || e.id),
  )
  // Collapse the forward (A→B) and reverse (B→A) edges into a single
  // connection with two direction toggles. Without collapsing the user
  // sees two overlapping wires for a bidirectional pair, which violates
  // the "one wire, two toggles" model the UI is built around.
  const byPair = new Map()
  for (const edge of edges) {
    const from = edge.from
    const to = edge.to_creature_id || edge.to
    const [low, high] = [from, to].sort()
    const pair = `${low}|${high}`
    if (!byPair.has(pair)) byPair.set(pair, { low, high, forward: null, reverse: null })
    const slot = byPair.get(pair)
    if (from === low) slot.forward = edge
    else slot.reverse = edge
  }
  for (const { low, high, forward, reverse } of byPair.values()) {
    const id = outputPairId(graphId, low, high)
    const sample = forward || reverse
    connections.push({
      id,
      a: low,
      b: high,
      groupId: graphId,
      label: "wire",
      brief: sample.prompt || "direct output wiring",
      details: sample.with_content === false ? "metadata-only output" : "forwards output content",
      routeOffset: layout.connections?.[id]?.routeOffset || 0,
      aToB: !!forward,
      bToA: !!reverse,
      backend: {
        kind: "output_edge",
        graphId,
        a: low,
        b: high,
        forwardEdgeId: forward ? forward.edge_id || forward.id : null,
        reverseEdgeId: reverse ? reverse.edge_id || reverse.id : null,
      },
    })
  }
}

export function normalizeSnapshot(snapshot, layout) {
  const nodes = []
  const groups = []
  const connections = []

  for (const [graphIndex, graph] of (snapshot?.graphs || []).entries()) {
    const graphId = graph.graph_id
    const creatureCount = (graph.creatures || []).length
    const channelCount = (graph.channels || []).length
    // A graph only earns a membrane when it actually contains a
    // multi-creature relationship or a channel. A freshly-spawned
    // single creature has no peer to share context with, so wrapping
    // it in a one-card molecule is just visual noise.
    const hasMembrane = creatureCount > 1 || channelCount > 0
    if (hasMembrane) {
      groups.push({
        id: graphId,
        label: graph.name || graphId,
        collapsed: layout.groups?.[graphId]?.collapsed === true,
        backend: graph,
      })
    }

    let itemIndex = 0
    for (const creature of graph.creatures || []) {
      if (addCreatureNode(nodes, creature, graphId, hasMembrane, graphIndex, itemIndex, layout)) {
        itemIndex += 1
      }
    }
    for (const channel of graph.channels || []) {
      addChannelNode(nodes, channel, graphId, graphIndex, itemIndex, layout)
      itemIndex += 1
    }
    addChannelEdges(connections, graph, layout)
    addOutputEdges(connections, graph, layout)
  }

  return {
    nodes: stableSortById(nodes),
    groups: stableSortById(groups),
    connections: stableSortById(connections),
  }
}
