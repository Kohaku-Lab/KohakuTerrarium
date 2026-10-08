/**
 * Projection: turns the graph-view model plus the user's view options into
 * the exact set of groups, nodes and edges one view renders. Pure.
 *
 * Options:
 *   sessionId    scope to one session, or null for every session
 *   groupBy      "auto" | "none" | "host" | "session"
 *   layers       visibility per link kind (LAYER_IDS): channel memberships, wires,
 *                lineage, a privileged creature's listens and assigned sends,
 *                and direct creature → creature reach
 *   channelMode  "node" (channels drawn as hubs) | "inline" (creature→creature edges labelled by channel)
 *   collapsed    Set of group ids rendered as one aggregate node
 *   search       case-insensitive name filter (non-matches are dimmed)
 *   focusId      node whose neighbourhood stays lit (others are dimmed)
 */

import { sortHosts } from "@/utils/graph/data/model"

export const DEFAULT_LAYERS = Object.freeze({
  channels: true,
  wires: true,
  lineage: false,
  privilegedListen: true,
  privilegedSend: true,
  direct: true,
})

export const LAYER_IDS = Object.freeze(Object.keys(DEFAULT_LAYERS))

/** A privileged creature's channel edge narrowed to the directions `layers` show, or null. */
function controlEdge(e, layers) {
  const send = layers.privilegedSend && (e.mode === "send" || e.mode === "both")
  const listen = layers.privilegedListen && (e.mode === "listen" || e.mode === "both")
  if (!send && !listen) return null
  const mode = send && listen ? "both" : send ? "send" : "listen"
  return mode === e.mode ? e : { ...e, mode }
}

export function groupIdFor(kind, key) {
  return `grp:${kind}:${key}`
}

/** Resolve "auto" to a concrete grouping for the current scope. */
export function resolveGroupBy(model, { sessionId, groupBy }) {
  if (groupBy && groupBy !== "auto") return groupBy
  if (!sessionId && model.sessions.length > 1) return "session"
  const creatures = sessionId
    ? model.creatures.filter((c) => c.sessionId === sessionId)
    : model.creatures
  return new Set(creatures.map((c) => c.hostId)).size > 1 ? "host" : "none"
}

function scopeModel(model, sessionId) {
  if (!sessionId) return model
  const keep = (x) => x.sessionId === sessionId
  return {
    ...model,
    sessions: model.sessions.filter((s) => s.id === sessionId),
    creatures: model.creatures.filter(keep),
    channels: model.channels.filter(keep),
    edges: model.edges.filter(keep),
  }
}

/**
 * Privileged creatures form the control plane: when a session has both
 * privileged creatures and workers, the privileged ones share one
 * "privileged nodes" group per session. Grouped by session, they stay in
 * their session's group instead, so the session boundary holds all of it.
 */
function controlSessions(scoped) {
  const out = new Set()
  for (const s of scoped.sessions) {
    const members = scoped.creatures.filter((c) => c.sessionId === s.id)
    if (members.some((c) => c.privileged) && members.some((c) => !c.privileged)) out.add(s.id)
  }
  return out
}

function controlLabel(sessions, sessionId) {
  if (sessions.length < 2) return ""
  return sessions.find((s) => s.id === sessionId)?.name || sessionId
}

/**
 * The privileged-node groups of a projection, for views that lay the control
 * plane out on its own (flow, bus). Grouped by session they are not render
 * groups, so they are derived here from the expanded sessions.
 */
export function controlGroupsOf(projection) {
  if (projection.groupBy !== "session") return projection.groups.filter((g) => g.kind === "control")
  const present = new Set(projection.nodes.map((n) => n.id))
  return [...controlSessions(projection)]
    .map((sessionId) => ({
      id: groupIdFor("control", sessionId),
      kind: "control",
      key: sessionId,
      label: controlLabel(projection.sessions, sessionId),
      creatureIds: projection.creatures
        .filter((c) => c.sessionId === sessionId && c.privileged && present.has(c.id))
        .map((c) => c.id),
      channelIds: [],
      busy: 0,
      attention: 0,
      collapsed: false,
    }))
    .filter((g) => g.creatureIds.length)
}

function buildGroups(scoped, groupBy, collapsed) {
  const groups = new Map()
  const memberOf = new Map()
  const sessionName = new Map(scoped.sessions.map((s) => [s.id, s.name]))
  const withControl = groupBy === "session" ? new Set() : controlSessions(scoped)
  const ensure = (kind, key, label) => {
    const id = groupIdFor(kind, key)
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        kind,
        key,
        label,
        creatureIds: [],
        channelIds: [],
        busy: 0,
        attention: 0,
        collapsed: collapsed.has(id),
      })
    }
    return groups.get(id)
  }
  for (const c of scoped.creatures) {
    let group = null
    if (c.privileged && withControl.has(c.sessionId)) {
      group = ensure("control", c.sessionId, controlLabel(scoped.sessions, c.sessionId))
    } else if (groupBy === "host") {
      group = ensure("host", c.hostId, c.hostId)
    } else if (groupBy === "session") {
      group = ensure("session", c.sessionId, sessionName.get(c.sessionId) || c.sessionId)
    }
    if (!group) continue
    group.creatureIds.push(c.id)
    if (c.status === "busy") group.busy += 1
    memberOf.set(c.id, group.id)
  }
  if (groupBy === "session") {
    for (const ch of scoped.channels) {
      const group = ensure("session", ch.sessionId, sessionName.get(ch.sessionId) || ch.sessionId)
      group.channelIds.push(ch.id)
      memberOf.set(ch.id, group.id)
    }
  }
  const hostOrder = sortHosts(
    [...groups.values()].filter((g) => g.kind === "host").map((g) => g.key),
  )
  const sessionOrder = scoped.sessions.map((s) => s.id)
  const rankOf = (g) => {
    const base = g.kind === "control" ? 0 : 1000
    const list = g.kind === "host" ? hostOrder : sessionOrder
    return base + Math.max(0, list.indexOf(g.key))
  }
  const sorted = new Map([...groups.entries()].sort(([, a], [, b]) => rankOf(a) - rankOf(b)))
  return { groups: sorted, memberOf }
}

function inlineChannelEdges(scoped, layers) {
  const privileged = new Set(scoped.creatures.filter((c) => c.privileged).map((c) => c.id))
  const sends = (id) => layers.privilegedSend || !privileged.has(id)
  const listens = (id) => layers.privilegedListen || !privileged.has(id)
  const out = []
  for (const ch of scoped.channels) {
    for (const s of ch.senders.filter(sends)) {
      for (const l of ch.listeners.filter(listens)) {
        if (s === l) continue
        out.push({
          id: `via:${s}:${l}:${ch.id}`,
          kind: "via",
          source: s,
          target: l,
          sessionId: ch.sessionId,
          channelName: ch.name,
        })
      }
    }
  }
  return out
}

function mergeEdges(edges, remap) {
  const merged = new Map()
  for (const e of edges) {
    const source = remap(e.source)
    const target = remap(e.target)
    if (!source || !target || source === target) continue
    const key = `${e.kind}|${e.mode || ""}|${source}|${target}`
    const existing = merged.get(key)
    if (existing) {
      existing.count += 1
      existing.memberIds.push(e.id)
      if (e.channelName && !existing.labels.includes(e.channelName))
        existing.labels.push(e.channelName)
      continue
    }
    merged.set(key, {
      ...e,
      id: source === e.source && target === e.target ? e.id : `agg:${key}`,
      source,
      target,
      count: 1,
      memberIds: [e.id],
      labels: e.channelName ? [e.channelName] : [],
    })
  }
  return [...merged.values()]
}

/** Ids lit around `focusId`: itself, its edges, neighbours, and members of a neighbouring channel. */
export function neighbourhoodOf(focusId, nodes, edges) {
  const lit = new Set([focusId])
  const isChannel = new Set(nodes.filter((n) => n.kind === "channel").map((n) => n.id))
  const touch = (id) => edges.filter((e) => e.source === id || e.target === id)
  for (const e of touch(focusId)) {
    lit.add(e.id)
    const other = e.source === focusId ? e.target : e.source
    lit.add(other)
    if (isChannel.has(other) && !isChannel.has(focusId)) {
      for (const e2 of touch(other)) {
        lit.add(e2.id)
        lit.add(e2.source === other ? e2.target : e2.source)
      }
    }
  }
  return lit
}

/** Build the render set for one view. */
export function projectGraph(model, options = {}) {
  const {
    sessionId = null,
    groupBy: rawGroupBy = "auto",
    layers = DEFAULT_LAYERS,
    channelMode = "node",
    collapsed = new Set(),
    search = "",
    focusId = null,
  } = options
  const scoped = scopeModel(model, sessionId)
  const groupBy = resolveGroupBy(model, { sessionId, groupBy: rawGroupBy })
  const { groups, memberOf } = buildGroups(scoped, groupBy, collapsed)
  const showChannelNodes = layers.channels && channelMode === "node"

  const nodes = []
  const hidden = new Set()
  for (const group of groups.values()) {
    if (group.collapsed) nodes.push({ id: group.id, kind: "aggregate", parent: null, group })
  }
  for (const c of scoped.creatures) {
    const groupId = memberOf.get(c.id) || null
    if (groupId && groups.get(groupId).collapsed) continue
    nodes.push({ id: c.id, kind: "creature", parent: groupId, creature: c })
  }
  for (const ch of scoped.channels) {
    if (!showChannelNodes) {
      hidden.add(ch.id)
      continue
    }
    const groupId = memberOf.get(ch.id) || null
    if (groupId && groups.get(groupId).collapsed) continue
    nodes.push({ id: ch.id, kind: "channel", parent: groupId, channel: ch })
  }

  let rawEdges = []
  for (const e of scoped.edges) {
    if (e.kind === "channel") {
      const shown = !showChannelNodes ? null : e.control ? controlEdge(e, layers) : e
      if (shown) rawEdges.push(shown)
    } else if (e.kind === "wire") {
      if (layers.wires) rawEdges.push(e)
    } else if (e.kind === "lineage") {
      if (layers.lineage) rawEdges.push(e)
    } else if (e.kind === "direct") {
      if (layers.direct) rawEdges.push(e)
    } else rawEdges.push(e)
  }
  if (layers.channels && channelMode === "inline")
    rawEdges = rawEdges.concat(inlineChannelEdges(scoped, layers))

  const remap = (id) => {
    if (hidden.has(id)) return null
    const groupId = memberOf.get(id)
    if (groupId && groups.get(groupId).collapsed) return groupId
    return id
  }
  const edges = mergeEdges(rawEdges, remap)

  const dimmed = new Set()
  const query = search.trim().toLowerCase()
  if (query) {
    const label = (n) => (n.creature?.name || n.channel?.name || n.group?.label || "").toLowerCase()
    const matched = new Set(nodes.filter((n) => label(n).includes(query)).map((n) => n.id))
    for (const n of nodes) if (!matched.has(n.id)) dimmed.add(n.id)
    for (const e of edges) if (!matched.has(e.source) && !matched.has(e.target)) dimmed.add(e.id)
  }
  if (focusId && nodes.some((n) => n.id === focusId)) {
    const lit = neighbourhoodOf(focusId, nodes, edges)
    for (const n of nodes) if (!lit.has(n.id)) dimmed.add(n.id)
    for (const e of edges) if (!lit.has(e.id)) dimmed.add(e.id)
  }

  return {
    groupBy,
    groups: [...groups.values()],
    nodes,
    edges,
    dimmed,
    sessions: scoped.sessions,
    channels: scoped.channels,
    creatures: scoped.creatures,
    stats: {
      creatures: scoped.creatures.length,
      channels: scoped.channels.length,
      wires: scoped.edges.filter((e) => e.kind === "wire").length,
      busy: scoped.creatures.filter((c) => c.status === "busy").length,
      hosts: new Set(scoped.creatures.map((c) => c.hostId)).size,
    },
  }
}
