/**
 * Graph-view model: normalizes a runtime-graph snapshot
 * (`GET /api/runtime/graph`) into sessions, hosts, creatures, channels and
 * typed edges. Pure — no store or DOM access.
 *
 * Edge kinds:
 *   channel  creature ↔ channel membership (`mode`: send | listen | both)
 *   wire     turn-end output wiring, creature → creature
 *   lineage  spawned-by link, parent creature → child creature
 *   direct   direct message reach, creature → creature: a privileged
 *            creature reaches every unprivileged member (`implicit`); any
 *            creature that sends on another's direct channel reaches its
 *            owner explicitly
 *
 * Channel edges of a privileged creature carry `control`: it listens on every
 * channel, and its send memberships are the ones assigned to it.
 *
 * A channel named after a creature of the same session is that creature's
 * direct channel, an alias for messaging it. It is not a channel node: it is
 * dropped from channels, channel edges and creature listen/send lists, drawn
 * as `direct` edges, and kept in `aliases` because the engine still counts
 * it as a link when predicting splits.
 */

export const HOST_SITE = "_host"

export function channelNodeId(sessionId, name) {
  return `ch:${sessionId}:${name}`
}

const BACKEND_STATUS = {
  not_started: "stopped",
  stopped: "stopped",
  idle: "idle",
  busy: "busy",
  error: "error",
}

/** Map backend creature fields onto busy | idle | paused | stopped | error. */
export function creatureStatus(raw) {
  if (raw.killed) return "stopped"
  if (raw.paused) return "paused"
  if (raw.status && BACKEND_STATUS[raw.status]) return BACKEND_STATUS[raw.status]
  if (raw.running === false) return "stopped"
  if (raw.is_processing) return "busy"
  return "idle"
}

function normalizeCreature(raw, sessionId, sessionHost) {
  return {
    id: raw.creature_id || raw.agent_id,
    name: raw.name || raw.creature_id || raw.agent_id,
    sessionId,
    hostId: raw.home_node || sessionHost,
    status: creatureStatus(raw),
    killed: !!raw.killed,
    privileged: !!(raw.is_privileged || raw.is_root),
    root: !!raw.is_root,
    parentId: raw.parent_creature_id || null,
    recipeRootId: raw.recipe_root_id || null,
    model: raw.model || raw.llm_name || "",
    llmName: raw.llm_name || "",
    provider: raw.provider || "",
    configName: raw.config_name || "",
    maxContext: raw.max_context || 0,
    tools: (raw.tools || []).length,
    subagents: [...(raw.subagents || [])],
    listen: [...(raw.listen_channels || [])],
    send: [...(raw.send_channels || [])],
  }
}

function addChannelEdges(creature, channelsByName, ensureChannel, edges) {
  const names = new Set([...creature.send, ...creature.listen])
  for (const name of names) {
    const channel = channelsByName.get(name) || ensureChannel(name)
    const sends = creature.send.includes(name)
    const listens = creature.listen.includes(name)
    if (sends) channel.senders.push(creature.id)
    if (listens) channel.listeners.push(creature.id)
    edges.push({
      id: `chan:${creature.id}:${channel.id}`,
      kind: "channel",
      mode: sends && listens ? "both" : sends ? "send" : "listen",
      source: creature.id,
      target: channel.id,
      sessionId: creature.sessionId,
      channelName: name,
      control: creature.privileged,
    })
  }
}

function addWireEdges(graph, sessionId, creatureIdByName, creatureIds, privilegedIds, edges) {
  for (const raw of graph.output_edges || []) {
    const from = raw.from
    const to = raw.to_creature_id || creatureIdByName.get(raw.to) || ""
    const edgeId = raw.edge_id || raw.id
    if (!from || !to || !edgeId || !creatureIds.has(from) || !creatureIds.has(to)) continue
    edges.push({
      id: `wire:${sessionId}:${from}:${edgeId}`,
      kind: "wire",
      source: from,
      target: to,
      sessionId,
      edgeId,
      withContent: raw.with_content !== false,
      prompt: raw.prompt || "",
      // Worker → privileged is a report up the control plane; privileged → worker is a deliberate dispatch.
      report: privilegedIds.has(to) && !privilegedIds.has(from),
      dispatch: privilegedIds.has(from) && !privilegedIds.has(to),
    })
  }
}

function addDirectEdges(members, aliasSenders, sessionId, edges) {
  const byName = new Map(members.map((c) => [c.name, c]))
  const pairs = new Map()
  const add = (source, target, implicit) => {
    if (source.id === target.id) return
    const key = `${source.id}>${target.id}`
    const prev = pairs.get(key)
    if (prev) prev.implicit = prev.implicit && implicit
    else pairs.set(key, { source: source.id, target: target.id, implicit })
  }
  for (const [name, senderIds] of aliasSenders) {
    const owner = byName.get(name)
    if (!owner) continue
    for (const id of senderIds) {
      const sender = members.find((c) => c.id === id)
      if (sender) add(sender, owner, false)
    }
  }
  for (const p of members.filter((c) => c.privileged))
    for (const target of members.filter((c) => !c.privileged)) add(p, target, true)
  for (const pair of pairs.values())
    edges.push({
      id: `direct:${pair.source}:${pair.target}`,
      kind: "direct",
      source: pair.source,
      target: pair.target,
      sessionId,
      implicit: pair.implicit,
    })
}

/** Build the full model from a snapshot. Unknown or partial fields degrade to defaults. */
export function buildGraphModel(snapshot) {
  const sessions = []
  const creatures = []
  const channels = []
  const edges = []
  const aliases = []
  const memberToSession = {}

  for (const graph of snapshot?.graphs || []) {
    const sessionId = graph.graph_id
    if (!sessionId) continue
    memberToSession[sessionId] = sessionId
    for (const member of graph.members || []) memberToSession[member.graph_id] = sessionId
    const sessionHost = graph.node_id || HOST_SITE
    const session = {
      id: sessionId,
      name: graph.name || sessionId,
      kind: graph.kind || "",
      isCluster: !!graph.is_cluster,
      configPath: graph.config_path || "",
      savedName: graph.session_name || "",
      creatureIds: [],
      channelIds: [],
      hostIds: [],
    }

    const rawCreatures = (graph.creatures || []).filter((raw) => raw.creature_id || raw.agent_id)
    const creatureNames = new Set(
      rawCreatures.map((raw) => raw.name || raw.creature_id || raw.agent_id),
    )
    const aliasMembers = new Map()
    const aliasSenders = new Map()
    const members = []
    const channelsByName = new Map()
    const ensureChannel = (name, raw = {}) => {
      const channel = {
        id: channelNodeId(sessionId, name),
        name,
        sessionId,
        description: raw.description || "",
        messageCount: raw.message_count || 0,
        senders: [],
        listeners: [],
      }
      channelsByName.set(name, channel)
      channels.push(channel)
      session.channelIds.push(channel.id)
      return channel
    }
    for (const raw of graph.channels || []) {
      if (raw?.name && !creatureNames.has(raw.name) && !channelsByName.has(raw.name))
        ensureChannel(raw.name, raw)
    }

    const creatureIdByName = new Map()
    const creatureIds = new Set()
    const privilegedIds = new Set()
    const sessionHosts = new Set()
    for (const raw of rawCreatures) {
      const creature = normalizeCreature(raw, sessionId, sessionHost)
      for (const name of new Set([...creature.listen, ...creature.send])) {
        if (!creatureNames.has(name)) continue
        if (!aliasMembers.has(name)) aliasMembers.set(name, [])
        aliasMembers.get(name).push(creature.id)
        if (!creature.send.includes(name)) continue
        if (!aliasSenders.has(name)) aliasSenders.set(name, [])
        aliasSenders.get(name).push(creature.id)
      }
      creature.listen = creature.listen.filter((name) => !creatureNames.has(name))
      creature.send = creature.send.filter((name) => !creatureNames.has(name))
      creatures.push(creature)
      members.push(creature)
      creatureIds.add(creature.id)
      if (creature.privileged) privilegedIds.add(creature.id)
      creatureIdByName.set(creature.name, creature.id)
      session.creatureIds.push(creature.id)
      sessionHosts.add(creature.hostId)
      addChannelEdges(creature, channelsByName, ensureChannel, edges)
    }
    addWireEdges(graph, sessionId, creatureIdByName, creatureIds, privilegedIds, edges)
    addDirectEdges(members, aliasSenders, sessionId, edges)
    for (const [name, memberIds] of aliasMembers) aliases.push({ sessionId, name, memberIds })
    session.hostIds = sortHosts([...sessionHosts])
    sessions.push(session)
  }

  const creatureById = new Map(creatures.map((c) => [c.id, c]))
  for (const child of creatures) {
    if (!child.parentId || !creatureById.has(child.parentId)) continue
    edges.push({
      id: `lin:${child.parentId}:${child.id}`,
      kind: "lineage",
      source: child.parentId,
      target: child.id,
      sessionId: child.sessionId,
    })
  }

  return {
    version: snapshot?.version || 0,
    sessions,
    creatures,
    channels,
    edges,
    aliases,
    hosts: sortHosts([...new Set(creatures.map((c) => c.hostId))]),
    memberToSession,
  }
}

/** Host first, then workers alphabetically. */
export function sortHosts(ids) {
  return [...ids].sort((a, b) => {
    if (a === b) return 0
    if (a === HOST_SITE) return -1
    if (b === HOST_SITE) return 1
    return a.localeCompare(b)
  })
}

/** Index lookups the views need repeatedly. */
export function indexModel(model) {
  const byId = new Map()
  for (const c of model.creatures) byId.set(c.id, { kind: "creature", item: c })
  for (const ch of model.channels) byId.set(ch.id, { kind: "channel", item: ch })
  for (const e of model.edges) byId.set(e.id, { kind: "edge", item: e })
  for (const s of model.sessions) byId.set(`session:${s.id}`, { kind: "session", item: s })
  return byId
}

/**
 * Predict a session's connected components after removing one creature or
 * one channel. Mirrors the engine rule: creatures are connected when they
 * share a channel or an output wire. Returns arrays of creature names,
 * largest first.
 */
export function predictComponents(
  model,
  sessionId,
  { removeCreature = null, removeChannel = null } = {},
) {
  const members = model.creatures.filter(
    (c) => c.sessionId === sessionId && c.id !== removeCreature,
  )
  const ids = new Set(members.map((c) => c.id))
  const parent = new Map(members.map((c) => [c.id, c.id]))
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)))
      x = parent.get(x)
    }
    return x
  }
  const union = (a, b) => parent.set(find(a), find(b))
  const byChannel = new Map()
  for (const e of model.edges) {
    if (e.sessionId !== sessionId) continue
    if (e.kind === "wire" && ids.has(e.source) && ids.has(e.target)) union(e.source, e.target)
    if (e.kind === "channel" && e.target !== removeChannel && ids.has(e.source)) {
      const first = byChannel.get(e.target)
      if (first) union(first, e.source)
      else byChannel.set(e.target, e.source)
    }
  }
  for (const alias of model.aliases || []) {
    if (alias.sessionId !== sessionId) continue
    const present = alias.memberIds.filter((id) => ids.has(id))
    for (const id of present.slice(1)) union(present[0], id)
  }
  const groups = new Map()
  for (const c of members) {
    const root = find(c.id)
    if (!groups.has(root)) groups.set(root, [])
    groups.get(root).push(c.name)
  }
  return [...groups.values()].sort((a, b) => b.length - a.length)
}

/** Components left after removing `creatureId` from its session. */
export function predictRemoval(model, creatureId) {
  const target = model.creatures.find((c) => c.id === creatureId)
  if (!target) return []
  return predictComponents(model, target.sessionId, { removeCreature: creatureId })
}

/** Components left after removing channel node `channelId` from its session. */
export function predictChannelRemoval(model, channelId) {
  const channel = model.channels.find((ch) => ch.id === channelId)
  if (!channel) return []
  return predictComponents(model, channel.sessionId, { removeChannel: channelId })
}
