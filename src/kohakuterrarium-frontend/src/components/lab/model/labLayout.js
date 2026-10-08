/**
 * The lab bench: every running session as a tank, laid out on one canvas.
 * One machine: tanks in rows, the start tile last. Several machines: one lane
 * per machine; a session on one machine sits in its lane, a session across
 * machines spans its lanes with one compartment per machine, each aligned
 * with that machine's lane. Pure.
 */

import { HOST_SITE, sortHosts } from "@/utils/graph/data/model"

export const TANK_W = 300
export const GAP = 28
export const LANE_PAD = 16
export const LANE_HEAD = 34
/** Width of one compartment's glass: the tank less its 8px glass inset on each side. */
export const GLASS_W = TANK_W - 16
const HEAD_H = 40
const FOOT_H = 30
const GLYPH = 26
const GLASS_PAD = 10
const MAX_ROWS = 4

const STATUS_RANK = { error: 4, busy: 3, paused: 2, idle: 1, stopped: 0 }

/** Glyph columns that fit a compartment `width` wide. */
export function glyphCols(width) {
  return Math.max(1, Math.floor((width - 2 * GLASS_PAD) / GLYPH))
}

/** Glass height for `count` creatures in compartments `width` wide (rows capped; extra show as "+N"). */
export function glassHeight(count, width) {
  const rows = Math.min(MAX_ROWS, Math.max(1, Math.ceil(count / glyphCols(width))))
  return rows * GLYPH + 2 * GLASS_PAD
}

/** Height of a tank whose fullest compartment holds `count` creatures. */
export function tankHeight(count) {
  return HEAD_H + glassHeight(count, GLASS_W) + FOOT_H
}

/** The status a whole tank shows: the most urgent of its creatures. */
export function tankStatus(creatures) {
  let best = "stopped"
  for (const c of creatures) if ((STATUS_RANK[c.status] ?? 0) > STATUS_RANK[best]) best = c.status
  return creatures.length ? best : "stopped"
}

/**
 * One tank per session of `model` (buildGraphModel output): its creatures
 * per machine (privileged first), status counts and channel ids.
 */
export function buildTanks(model) {
  return model.sessions.map((s) => {
    const creatures = model.creatures.filter((c) => c.sessionId === s.id)
    const hosts = sortHosts([...new Set(creatures.map((c) => c.hostId || HOST_SITE))])
    const byHost = (h) =>
      creatures
        .filter((c) => (c.hostId || HOST_SITE) === h)
        .sort((a, b) => Number(b.privileged) - Number(a.privileged))
    const counts = {}
    for (const c of creatures) counts[c.status] = (counts[c.status] || 0) + 1
    return {
      id: s.id,
      name: s.name,
      hosts: hosts.length ? hosts : [HOST_SITE],
      compartments: (hosts.length ? hosts : [HOST_SITE]).map((h) => ({
        hostId: h,
        creatures: byHost(h),
      })),
      size: creatures.length,
      counts,
      status: tankStatus(creatures),
      channelIds: [...s.channelIds],
    }
  })
}

function singleHostLayout(tanks, width) {
  const cols = Math.max(1, Math.floor((width + GAP) / (TANK_W + GAP)))
  const cells = [
    ...tanks.map((t) => ({ tank: t, h: tankHeight(t.size) })),
    { start: true, h: tankHeight(1) },
  ]
  const placed = []
  let y = 0
  for (let i = 0; i < cells.length; i += cols) {
    const row = cells.slice(i, i + cols)
    const rowH = Math.max(...row.map((c) => c.h))
    row.forEach((c, j) => {
      const box = { x: j * (TANK_W + GAP), y, w: TANK_W, h: c.h }
      placed.push(
        c.start
          ? { kind: "start", ...box }
          : {
              kind: "tank",
              tank: c.tank,
              ...box,
              compartments: [
                {
                  hostId: c.tank.hosts[0],
                  x: 0,
                  w: TANK_W,
                  creatures: c.tank.compartments[0].creatures,
                },
              ],
            },
      )
    })
    y += rowH + GAP
  }
  return { lanes: [], items: placed }
}

function multiHostLayout(tanks, hosts) {
  const laneW = TANK_W + 2 * LANE_PAD
  const laneX = new Map(hosts.map((h, i) => [h, i * (laneW + GAP)]))
  const index = new Map(hosts.map((h, i) => [h, i]))
  const items = []
  const spanning = tanks
    .filter((t) => t.hosts.length > 1)
    .map((t) => ({
      t,
      from: Math.min(...t.hosts.map((h) => index.get(h))),
      to: Math.max(...t.hosts.map((h) => index.get(h))),
    }))
  spanning.sort((a, b) => a.from - b.from || b.to - a.to)
  // Spanning tanks share a row when their lane ranges do not overlap.
  const rows = []
  for (const s of spanning) {
    const row = rows.find((r) => r.every((o) => o.to < s.from || o.from > s.to))
    if (row) row.push(s)
    else rows.push([s])
  }
  let y = LANE_HEAD
  for (const row of rows) {
    let rowH = 0
    for (const { t, from, to } of row) {
      const x = laneX.get(hosts[from]) + LANE_PAD
      const w = laneX.get(hosts[to]) + laneW - LANE_PAD - x
      const h = tankHeight(Math.max(...t.compartments.map((c) => c.creatures.length)))
      items.push({
        kind: "tank",
        tank: t,
        x,
        y,
        w,
        h,
        compartments: t.compartments.map((c) => ({
          hostId: c.hostId,
          x: laneX.get(c.hostId) + LANE_PAD - x,
          w: TANK_W,
          creatures: c.creatures,
        })),
      })
      rowH = Math.max(rowH, h)
    }
    y += rowH + GAP
  }
  const laneY = new Map(hosts.map((h) => [h, y]))
  for (const t of tanks.filter((x) => x.hosts.length === 1)) {
    const h = t.hosts[0]
    const ty = laneY.get(h)
    const height = tankHeight(t.size)
    items.push({
      kind: "tank",
      tank: t,
      x: laneX.get(h) + LANE_PAD,
      y: ty,
      w: TANK_W,
      h: height,
      compartments: [{ hostId: h, x: 0, w: TANK_W, creatures: t.compartments[0].creatures }],
    })
    laneY.set(h, ty + height + GAP)
  }
  const first = hosts[0]
  const startH = tankHeight(1)
  items.push({
    kind: "start",
    x: laneX.get(first) + LANE_PAD,
    y: laneY.get(first),
    w: TANK_W,
    h: startH,
  })
  laneY.set(first, laneY.get(first) + startH + GAP)
  const bottom = Math.max(...laneY.values())
  return {
    lanes: hosts.map((h) => ({ hostId: h, x: laneX.get(h), y: 0, w: laneW, h: bottom })),
    items,
  }
}

/**
 * Place `tanks` for a canvas `width` wide (at scale 1). Returns {lanes,
 * items, bounds}: lanes are machine columns (empty with one machine); items
 * are tanks (with compartments positioned inside them) and the start tile.
 */
export function layoutBench(tanks, { width = 1200 } = {}) {
  const hosts = sortHosts([...new Set(tanks.flatMap((t) => t.hosts))])
  const laid = hosts.length > 1 ? multiHostLayout(tanks, hosts) : singleHostLayout(tanks, width)
  const boxes = [...laid.lanes, ...laid.items]
  const bounds = {
    w: Math.max(TANK_W, ...boxes.map((b) => b.x + b.w)),
    h: Math.max(1, ...boxes.map((b) => b.y + b.h)),
  }
  return { ...laid, bounds }
}

/** Scale and offset that fit `bounds` into a `vw`×`vh` viewport, never enlarging past 1; centred across, from the top. */
export function fitView(bounds, vw, vh, pad = 32) {
  const k = Math.max(0.25, Math.min(1, (vw - 2 * pad) / bounds.w, (vh - 2 * pad) / bounds.h))
  return { k, x: (vw - bounds.w * k) / 2, y: pad }
}
