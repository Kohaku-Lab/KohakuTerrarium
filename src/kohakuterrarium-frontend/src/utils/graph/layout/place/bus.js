/**
 * Bus view model: creatures are columns (grouped by host or session), each
 * channel is a row whose cells hold that creature's membership, and each
 * output wire is a row running from its source column to its target column.
 * Privileged-node columns (the session's control group) keep their cells, but a
 * channel's rail spans only its worker members. Pure.
 */

/** Membership cycle used by a cell click: none → listen → send → both → none. */
export const MEMBERSHIP_CYCLE = ["none", "listen", "send", "both"]

export function nextMembership(mode) {
  const i = MEMBERSHIP_CYCLE.indexOf(mode || "none")
  return MEMBERSHIP_CYCLE[(i + 1) % MEMBERSHIP_CYCLE.length]
}

function columnOrder(a, b) {
  if (a.creature.privileged !== b.creature.privileged) return a.creature.privileged ? -1 : 1
  return a.creature.name.localeCompare(b.creature.name)
}

function spanOf(indices) {
  return indices.length ? [Math.min(...indices), Math.max(...indices)] : null
}

/** Build {columns, bands, channelRows, wireRows} from a projection with channel nodes. */
export function busModel(projection) {
  const groupOrder = new Map(projection.groups.map((g, i) => [g.id, i]))
  const creatureNodes = projection.nodes.filter((n) => n.kind === "creature")
  const buckets = new Map()
  for (const n of creatureNodes) {
    const key = n.parent || ""
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(n)
  }
  const keys = [...buckets.keys()].sort(
    (a, b) => (groupOrder.get(a) ?? -1) - (groupOrder.get(b) ?? -1),
  )

  const columns = []
  const bands = []
  for (const key of keys) {
    const start = columns.length
    const group = key ? projection.groups.find((g) => g.id === key) : null
    for (const n of buckets.get(key).sort(columnOrder))
      columns.push({
        id: n.id,
        creature: n.creature,
        groupId: key || null,
        control: group?.kind === "control",
      })
    if (key) {
      bands.push({
        id: key,
        label: group?.label || key,
        kind: group?.kind,
        group,
        start,
        span: columns.length - start,
      })
    }
  }
  const colIndex = new Map(columns.map((c, i) => [c.id, i]))

  const modes = new Map()
  for (const e of projection.edges) {
    if (e.kind !== "channel" || !colIndex.has(e.source)) continue
    if (!modes.has(e.target)) modes.set(e.target, new Map())
    modes.get(e.target).set(e.source, { mode: e.mode, edgeId: e.id })
  }
  const channelRows = projection.nodes
    .filter((n) => n.kind === "channel")
    .map((n) => {
      const cells = modes.get(n.id) || new Map()
      const members = [...cells.keys()].map((id) => colIndex.get(id))
      const work = members.filter((i) => !columns[i].control)
      return {
        id: n.id,
        channel: n.channel,
        cells,
        span: spanOf(work.length ? work : members),
      }
    })

  const wireRows = projection.edges
    .filter((e) => e.kind === "wire" && colIndex.has(e.source) && colIndex.has(e.target))
    .map((e) => ({
      id: e.id,
      edge: e,
      from: colIndex.get(e.source),
      to: colIndex.get(e.target),
      span: spanOf([colIndex.get(e.source), colIndex.get(e.target)]),
    }))

  return { columns, bands, channelRows, wireRows }
}
