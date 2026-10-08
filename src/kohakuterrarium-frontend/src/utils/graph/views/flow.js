/**
 * Flow view input: how work moves from stage to stage. Unlike Network it
 * draws no channel nodes and no memberships, only handoffs.
 *
 * - Stages are workers. One handoff arrow per worker → worker pair whose work
 *   passes along (a channel one sends on and the other listens to, or a
 *   wire), labelled on the line with what carries it.
 * - A room — a channel every member both sends on and listens to — has no
 *   direction; it is a chip on its members' cards, not edges.
 * - Privileged nodes sit in their control group. Work enters through them: a
 *   channel no worker sends on becomes an arrow from the privileged nodes
 *   that send on it (all of them when none is listed) to each listener; a
 *   channel no worker listens to becomes an arrow from each sender back to
 *   them. A wire between a privileged node and a stage joins that pair's
 *   arrow, else it is its own arrow (dashed when a ping). Links every
 *   privileged node shares start once at the group's hub. How these
 *   channels are drawn is a knob (see `buildFlowInput`).
 * - Collect arrows are laid out privileged-first (`layoutReverse`), the arrow
 *   still pointing at the privileged node.
 * - Direction is distance from the nodes nothing feeds along handoffs and
 *   entry arrows; a handoff back to an earlier or equal rank is a return.
 * - Host / session groups are not containers here: each stage names its host
 *   (`multiHost`). Collapsed groups stay single aggregate stages.
 * - Direct links (see model.js) are `side` edges: drawn into a card's side
 *   across the flow, never ranking or placing anything.
 * Pure.
 */

import { controlGroupsOf } from "@/utils/graph/data/projection"
import { NODE_SIZE, flowEndpoints } from "@/utils/graph/layout/place/elk"

const CHIP_ROW = 18
const MAX_STAGE_WIDTH = 340
const chipWidth = (text) => Math.ceil(text.length * 6 + 14)

/**
 * A stage card's size: one more row for privileged chips beside a room row,
 * and wide enough for its privileged chips (up to MAX_STAGE_WIDTH), or the
 * default size (undefined) when it carries none.
 */
export function stageSize(stage) {
  const chips = [
    ...stage.inlets.map((n) => `↓ ${n}`),
    ...stage.outlets.map((n) => `↑ ${n}`),
    ...stage.wireChips.map((w) => `${w.entering ? "←" : "→"} ${w.name}`),
  ]
  if (!chips.length) return undefined
  const row = 44 + chips.reduce((sum, c) => sum + chipWidth(c) + 4, 0)
  return {
    width: Math.min(MAX_STAGE_WIDTH, Math.max(NODE_SIZE.stage.width, row)),
    height: NODE_SIZE.stage.height + (stage.rooms.length ? CHIP_ROW : 0),
  }
}

export function hubNodeId(groupId) {
  return `hub:${groupId}`
}

/** Label for a handoff or entry / collect arrow: the channels it carries. */
export function flowLabel(edge) {
  return (edge.labels || []).join(" · ")
}

/** A room: every member both sends and listens, so the channel carries no direction. */
export function isRoom(senders, listeners) {
  return (
    senders.size > 1 &&
    senders.size === listeners.size &&
    [...senders].every((id) => listeners.has(id))
  )
}

function controlSets(projection) {
  const privileged = new Set(projection.creatures.filter((c) => c.privileged).map((c) => c.id))
  const controlGroups = controlGroupsOf(projection)
  const viaGroup = new Map()
  for (const g of projection.groups) {
    if (g.collapsed && g.kind !== "control") for (const id of g.creatureIds) viaGroup.set(id, g.id)
  }
  const present = new Set(projection.nodes.map((n) => n.id))
  const resolve = (id) => (present.has(id) ? id : viaGroup.get(id) || null)
  return { privileged, controlGroups, resolve }
}

function stageNodes(projection, controlGroups) {
  const controlIds = new Set(controlGroups.map((g) => g.id))
  const groupOf = new Map(controlGroups.map((g) => [g.key, g.id]))
  const stages = []
  const privilegedNodes = []
  const addPrivileged = (c) =>
    privilegedNodes.push({
      id: c.id,
      kind: "privileged",
      parent: groupOf.get(c.sessionId) || null,
      sessionId: c.sessionId,
      creature: c,
    })
  for (const n of projection.nodes) {
    if (n.kind === "creature") {
      if (n.creature.privileged) addPrivileged(n.creature)
      else
        stages.push({
          id: n.id,
          kind: "stage",
          parent: null,
          sessionId: n.creature.sessionId,
          creature: n.creature,
          rooms: [],
          inlets: [],
          outlets: [],
          wireChips: [],
        })
    } else if (n.kind === "aggregate") {
      const members = n.group.creatureIds
        .map((id) => projection.creatures.find((x) => x.id === id))
        .filter(Boolean)
      if (controlIds.has(n.id)) members.forEach(addPrivileged)
      else
        stages.push({
          ...n,
          sessionId: members[0]?.sessionId || null,
          rooms: [],
          inlets: [],
          outlets: [],
          wireChips: [],
        })
    }
  }
  return { stages, privilegedNodes }
}

/**
 * Collects labelled links per (kind, source, target) so each pair is one
 * arrow; `members` keeps the model edges (channel memberships) it stands for.
 */
function linkBag() {
  const links = new Map()
  return {
    has: (key) => links.has(key),
    add(key, base, label, members = []) {
      if (!links.has(key)) links.set(key, { ...base, labels: [], members: [] })
      const link = links.get(key)
      if (label && !link.labels.includes(label)) link.labels.push(label)
      for (const m of members) if (!link.members.includes(m)) link.members.push(m)
    },
    list: () => [...links.values()],
  }
}

const membership = (creatureId, channel) => `chan:${creatureId}:${channel.id}`

export const PRIVILEGED_LINK_MODES = ["bundle", "chips", "pair"]
export const DEFAULT_PRIVILEGED_LINKS = "chips"

/**
 * Build {nodes, edges, groups, bundles, dock, multiHost} for the Flow view.
 * `privilegedLinks` draws the channels between privileged nodes and stages:
 * "bundle" — one arrow per stage, a channel every arrow of a fan carries
 * labelled once on the fan (`bundles`); "chips" (default) — no arrows, a
 * chip per channel and wire on each stage card; "pair" — one labelled arrow
 * per stage.
 */
export function buildFlowInput(projection, { privilegedLinks = DEFAULT_PRIVILEGED_LINKS } = {}) {
  const { privileged, controlGroups, resolve } = controlSets(projection)
  const { stages, privilegedNodes } = stageNodes(projection, controlGroups)
  const stageById = new Map(stages.map((n) => [n.id, n]))
  const pids = new Set(privilegedNodes.map((p) => p.id))
  const bySession = new Map()
  for (const p of privilegedNodes) {
    if (!bySession.has(p.sessionId)) bySession.set(p.sessionId, [])
    bySession.get(p.sessionId).push(p)
  }

  const hubs = new Map()
  const hubFor = (group, all) => {
    if (!hubs.has(group))
      hubs.set(group, {
        node: {
          id: hubNodeId(group),
          kind: "hub",
          parent: group,
          sessionId: all[0].sessionId,
          count: all.length,
        },
        order: all.map((p) => ({
          id: `hubl:${p.id}`,
          kind: "layout",
          layoutOnly: true,
          source: p.id,
          target: hubNodeId(group),
        })),
      })
    return hubs.get(group).node.id
  }
  // The privileged ends of an entry / collect arrow: the listed ones, or all; the hub when all share it.
  const controlEnds = (listed, sessionId) => {
    const all = bySession.get(sessionId) || []
    const ends = listed.length ? listed : all.map((p) => p.id)
    const group = all[0]?.parent
    return group && all.length > 1 && ends.length === all.length ? [hubFor(group, all)] : ends
  }

  const handoffs = linkBag()
  const control = linkBag()
  for (const ch of projection.channels) {
    const senders = new Set(ch.senders.map(resolve).filter((id) => stageById.has(id)))
    const listeners = new Set(ch.listeners.map(resolve).filter((id) => stageById.has(id)))
    if (!senders.size && !listeners.size) continue
    if (isRoom(senders, listeners)) {
      for (const id of senders) stageById.get(id).rooms.push(ch.name)
      continue
    }
    if (senders.size && listeners.size) {
      for (const s of senders)
        for (const l of listeners)
          if (s !== l)
            handoffs.add(
              `${s}>${l}`,
              { id: `hand:${s}:${l}`, kind: "via", source: s, target: l, sessionId: ch.sessionId },
              ch.name,
              [membership(s, ch), membership(l, ch)],
            )
      continue
    }
    const entering = !senders.size
    if (privilegedLinks === "chips") {
      for (const id of entering ? listeners : senders)
        stageById.get(id)[entering ? "inlets" : "outlets"].push(ch.name)
      continue
    }
    const listed = (entering ? ch.senders : ch.listeners).filter((id) => pids.has(id))
    for (const end of controlEnds(listed, ch.sessionId))
      for (const stage of entering ? listeners : senders) {
        const ends = end.startsWith("hub:") ? listed : [end]
        control.add(
          `${end}>${stage}>${entering}`,
          {
            id: `${entering ? "in" : "out"}:${end}:${stage}`,
            kind: "channel",
            control: true,
            shared: end.startsWith("hub:"),
            source: entering ? end : stage,
            target: entering ? stage : end,
            layoutReverse: !entering,
            sessionId: ch.sessionId,
          },
          ch.name,
          [...ends.map((p) => membership(p, ch)), membership(stage, ch)],
        )
      }
  }

  const wires = []
  const pings = new Set()
  const nameOf = new Map(privilegedNodes.map((p) => [p.id, p.creature.name]))
  for (const e of projection.edges) {
    if (e.kind !== "wire") continue
    const s = pids.has(e.source) ? e.source : resolve(e.source)
    const t = pids.has(e.target) ? e.target : resolve(e.target)
    const entering = pids.has(s) && stageById.has(t)
    const collecting = stageById.has(s) && pids.has(t)
    if (entering || collecting) {
      const end = entering ? s : t
      const stage = entering ? t : s
      if (privilegedLinks === "chips") {
        const name = nameOf.get(end)
        stageById
          .get(stage)
          .wireChips.push({ id: e.id, name, entering, ping: e.withContent === false })
        continue
      }
      // A wire between a privileged node and a stage joins the arrow already drawn for that pair.
      const key = `${end}>${stage}>${entering}`
      if (e.withContent === false) pings.add(e.id)
      if (control.has(key)) control.add(key, null, null, [e.id])
      else wires.push({ ...e, source: s, target: t, control: true, layoutReverse: collecting })
    } else if (stageById.has(s) && stageById.has(t) && s !== t)
      wires.push({ ...e, source: s, target: t })
  }
  const direct = new Map()
  for (const e of projection.edges) {
    if (e.kind !== "direct") continue
    const s = pids.has(e.source) ? e.source : resolve(e.source)
    const t = pids.has(e.target) ? e.target : resolve(e.target)
    if (!s || !t || s === t || direct.has(`${s}>${t}`)) continue
    direct.set(`${s}>${t}`, { ...e, source: s, target: t, side: true })
  }
  for (const s of stages) s.size = stageSize(s)

  const hubNodes = [...hubs.values()].map((h) => h.node)
  const order = [...hubs.values()].flatMap((h) => h.order)
  const handoffEdges = handoffs.list().map((e) => ({ ...e, layoutLabel: flowLabel(e) }))
  const controlEdges = control.list().map((e) => ({
    ...e,
    // Dashed only when nothing but pings travel it; a channel beside a ping still delivers content.
    ping: !e.labels.length && e.members.length > 0 && e.members.every((id) => pings.has(id)),
  }))
  const bundles = privilegedLinks === "bundle" ? bundleFans(controlEdges) : []
  for (const e of controlEdges) e.layoutLabel = flowLabel(e) || undefined
  const workEdges = [...handoffEdges, ...wires.filter((w) => !w.control)]
  const nodes = [...privilegedNodes, ...hubNodes, ...stages]
  const edges = [
    ...workEdges,
    ...controlEdges,
    ...wires.filter((w) => w.control),
    ...order,
    ...direct.values(),
  ]
  // Ranks follow work entering and passing along; collect arrows, control wires and layout-only edges do not set them.
  // A stage with an inlet chip is where work enters, as an entry arrow would say.
  const entries = new Set(
    stages.filter((s) => s.inlets.length || s.wireChips.some((w) => w.entering)).map((s) => s.id),
  )
  const rank = rankFlow(
    nodes.map((n) => n.id),
    edges.filter(
      (e) => !e.layoutOnly && !e.layoutReverse && !e.side && !(e.kind === "wire" && e.control),
    ),
    entries,
  )
  for (const n of nodes) n.rank = rank.get(n.id) ?? 0
  for (const p of privilegedNodes) p.pin = "first"
  for (const e of workEdges) e.back = (rank.get(e.target) ?? 0) <= (rank.get(e.source) ?? 0)

  const used = new Set(privilegedNodes.map((p) => p.parent).filter(Boolean))
  const hubOf = new Map(hubNodes.map((h) => [h.parent, h.id]))
  const [only] = used.size === 1 ? [...used] : []
  return {
    nodes,
    edges,
    bundles,
    groups: controlGroups
      .filter((g) => used.has(g.id))
      .map((g) => ({
        ...g,
        collapsed: false,
        creatureIds: g.creatureIds.filter((id) => privileged.has(id)),
        extraIds: hubOf.has(g.id) ? [hubOf.get(g.id)] : [],
      })),
    // One control group in scope: its side is a layout choice (see layout/dock.js).
    dock: only
      ? {
          groupId: only,
          memberIds: [
            ...privilegedNodes.filter((p) => p.parent === only).map((p) => p.id),
            ...(hubOf.has(only) ? [hubOf.get(only)] : []),
          ],
        }
      : null,
    multiHost: projection.stats.hosts > 1,
  }
}

/**
 * Fans of control arrows: all arrows into one privileged end (a fan-in) or
 * out of it (a fan-out). A channel every arrow of a fan of two or more
 * carries moves off the arrows into one bundle label for the fan; the
 * arrows keep only what sets them apart. Mutates `edges`' labels; returns
 * the bundles [{id, text, channels, members, end}].
 */
function bundleFans(edges) {
  const fans = new Map()
  for (const e of edges) {
    const end = e.layoutReverse ? e.target : e.source
    const key = `${end}>${e.layoutReverse ? "in" : "out"}`
    if (!fans.has(key)) fans.set(key, { end, edges: [] })
    fans.get(key).edges.push(e)
  }
  const bundles = []
  for (const [key, fan] of fans) {
    if (fan.edges.length < 2) continue
    const shared = fan.edges[0].labels.filter((name) =>
      fan.edges.every((e) => e.labels.includes(name)),
    )
    if (!shared.length) continue
    for (const e of fan.edges) {
      e.labels = e.labels.filter((name) => !shared.includes(name))
      e.bundled = shared
    }
    bundles.push({
      id: `bundle:${key}`,
      text: shared.join(" · "),
      channels: shared,
      members: fan.edges.map((e) => e.id),
      end: fan.end,
    })
  }
  return bundles
}

/**
 * Rank nodes by distance from the entry points (plus every node with no
 * incoming edge); nodes no entry reaches are seeded from the one with the
 * most outgoing over incoming edges. Returns Map id → rank.
 */
export function rankFlow(nodeIds, edges, entries = new Set()) {
  const out = new Map(nodeIds.map((id) => [id, []]))
  const indeg = new Map(nodeIds.map((id) => [id, 0]))
  for (const e of edges) {
    const [s, t] = flowEndpoints(e)
    if (!out.has(s) || !out.has(t) || s === t) continue
    out.get(s).push(t)
    indeg.set(t, indeg.get(t) + 1)
  }
  const rank = new Map()
  const bfs = (seeds, base) => {
    let frontier = seeds.filter((id) => !rank.has(id))
    for (const id of frontier) rank.set(id, base)
    let level = base
    while (frontier.length) {
      level += 1
      const next = []
      for (const id of frontier) {
        for (const t of out.get(id)) {
          if (rank.has(t)) continue
          rank.set(t, level)
          next.push(t)
        }
      }
      frontier = next
    }
  }
  const seeds = nodeIds.filter((id) => entries.has(id) || indeg.get(id) === 0)
  bfs(seeds, 0)
  while (rank.size < nodeIds.length) {
    const rest = nodeIds.filter((id) => !rank.has(id))
    const score = (id) => out.get(id).length - indeg.get(id)
    rest.sort((a, b) => score(b) - score(a))
    bfs([rest[0]], rank.size ? Math.max(...rank.values()) + 1 : 0)
  }
  return rank
}
