/**
 * Docking: lay a projection out without one group (the privileged nodes),
 * then place that group as a compact block on one side of the result. The
 * side is a layout choice like any other, scored by the place-and-route cost,
 * so the block goes where the drawing best fits the viewport instead of
 * always heading the flow. Pure.
 */

import { segmentHitsBox, sharedRun } from "@/utils/graph/layout/metrics"
import {
  GROUP_PAD,
  flowEndpoints,
  groupBackdrops,
  labelSize,
  sizeOf,
} from "@/utils/graph/layout/place/elk"
import { orthogonalRoute } from "@/utils/graph/layout/route/route"

export const DOCK_SIDES = ["top", "left", "right", "bottom"]

const GAP = 16
const MARGIN = 64
const LANES = 4
const LANE_STEP = 8
const LABEL_GAP = 6
const SHARE_CREDIT = 0.9
const PORT_SHIFT = 8

/** Dock spec for the projection's privileged-node group, when exactly one is expanded. */
export function controlDock(projection) {
  const groups = projection.groups.filter((g) => g.kind === "control" && !g.collapsed)
  if (groups.length !== 1) return null
  const memberIds = projection.nodes.filter((n) => n.parent === groups[0].id).map((n) => n.id)
  return memberIds.length ? { groupId: groups[0].id, memberIds } : null
}

/** A finished layout with the docked members' boxes and routes taken out, everything else kept. */
export function stripDock(placed, projection) {
  const members = new Set(projection.dock.memberIds)
  const touching = new Set(
    projection.edges.filter((e) => members.has(e.source) || members.has(e.target)).map((e) => e.id),
  )
  return {
    boxes: new Map([...placed.boxes].filter(([id]) => !members.has(id))),
    routes: new Map([...(placed.routes || new Map())].filter(([id]) => !touching.has(id))),
  }
}

/** The projection without the docked members and every edge touching them. */
export function withoutDock(projection) {
  const members = new Set(projection.dock.memberIds)
  return {
    ...projection,
    nodes: projection.nodes.filter((n) => !members.has(n.id)),
    edges: projection.edges.filter((e) => !members.has(e.source) && !members.has(e.target)),
    groups: projection.groups.filter((g) => g.id !== projection.dock.groupId),
    dock: null,
  }
}

function bounds(boxes) {
  const list = [...boxes.values()]
  if (!list.length) return { x0: 0, y0: 0, x1: 0, y1: 0 }
  return {
    x0: Math.min(...list.map((b) => b.x)),
    y0: Math.min(...list.map((b) => b.y)),
    x1: Math.max(...list.map((b) => b.x + b.width)),
    y1: Math.max(...list.map((b) => b.y + b.height)),
  }
}

/**
 * Pack member sizes into rows (top / bottom: no wider than `span`) or
 * columns (left / right: no taller than `span`). Returns offsets and extent.
 */
function packBlock(sizes, side, span) {
  const across = side === "top" || side === "bottom"
  const offsets = []
  let main = 0
  let cross = 0
  let lineCross = 0
  let extentMain = 0
  for (const s of sizes) {
    const len = across ? s.width : s.height
    const thick = across ? s.height : s.width
    if (main > 0 && main + len > span) {
      cross += lineCross + GAP
      main = 0
      lineCross = 0
    }
    offsets.push(across ? { x: main, y: cross } : { x: cross, y: main })
    main += len + GAP
    extentMain = Math.max(extentMain, main - GAP)
    lineCross = Math.max(lineCross, thick)
  }
  const extentCross = cross + lineCross
  return {
    offsets,
    width: across ? extentMain : extentCross,
    height: across ? extentCross : extentMain,
  }
}

/**
 * Place the docked group of `projection` on `side` of `base` (a layout of
 * `withoutDock(projection)`). Members keep their input order; edges that
 * touch them get right-angle routes. Returns {boxes, routes}.
 */
export function dockBlock(base, projection, side) {
  const { ids, sizes } = dockMembers(projection)
  const b = bounds(base.boxes)
  const span =
    side === "top" || side === "bottom" ? Math.max(b.x1 - b.x0, 1) : Math.max(b.y1 - b.y0, 1)
  const block = packBlock(sizes, side, span)
  const cx = (b.x0 + b.x1) / 2
  const cy = (b.y0 + b.y1) / 2
  const pad = GROUP_PAD
  const [roomX, roomY] = labelRoom(projection)
  const m = MARGIN + (side === "left" || side === "right" ? roomX : roomY)
  const origin = {
    top: { x: cx - block.width / 2, y: b.y0 - m - pad.bottom - block.height },
    bottom: { x: cx - block.width / 2, y: b.y1 + m + pad.top },
    left: { x: b.x0 - m - pad.side - block.width, y: cy - block.height / 2 },
    right: { x: b.x1 + m + pad.side, y: cy - block.height / 2 },
  }[side]
  // The gutter runs by the block; the label room lies between it and the drawing.
  const gutter = {
    top: b.y0 - m + MARGIN / 2,
    bottom: b.y1 + m - MARGIN / 2,
    left: b.x0 - m + MARGIN / 2,
    right: b.x1 + m - MARGIN / 2,
  }[side]
  return placeBlock(base, projection, ids, sizes, block, origin, () => [{ side, gutter }])
}

function dockMembers(projection) {
  const byId = new Map(projection.nodes.map((n) => [n.id, n]))
  const ids = projection.dock.memberIds.filter((id) => byId.has(id))
  return { ids, sizes: ids.map((id) => sizeOf(byId.get(id))) }
}

/**
 * Put the block at `origin` and route every edge touching it. `routeSide`
 * (targetBox, blockRect) picks the side the link leaves from and its gutter.
 */
function placeBlock(base, projection, ids, sizes, block, origin, routeSides) {
  const boxes = new Map(base.boxes)
  ids.forEach((id, i) =>
    boxes.set(id, {
      x: origin.x + block.offsets[i].x,
      y: origin.y + block.offsets[i].y,
      width: sizes[i].width,
      height: sizes[i].height,
    }),
  )
  const rect = { x: origin.x, y: origin.y, width: block.width, height: block.height }
  const members = new Set(ids)
  const routes = new Map(base.routes || [])
  // One gutter lane per docked node, direction and link family, stepping away from the block:
  // only channel links or only direct links sharing an end and a direction share a run.
  const lane = new Map()
  const away = { left: 1, top: 1, right: -1, bottom: -1 }
  const trunks = new Map()
  const linkCount = new Map()
  const pairDirs = new Map()
  const trunkOf = (e) => {
    const docked = members.has(e.source) ? e.source : e.target
    const other = docked === e.source ? e.target : e.source
    const family = e.kind === "direct" ? "direct>" : ""
    return { docked, other, key: `${docked}>${family}${docked === e.source ? "out" : "in"}` }
  }
  for (const e of projection.edges) {
    if (members.has(e.source) === members.has(e.target)) continue
    const { docked, other, key } = trunkOf(e)
    if (!lane.has(key)) lane.set(key, lane.size % LANES)
    linkCount.set(key, (linkCount.get(key) || 0) + 1)
    const pair = `${docked}|${other}`
    if (!pairDirs.has(pair)) pairDirs.set(pair, new Set())
    pairDirs.get(pair).add(key)
  }
  for (const e of projection.edges) {
    if (!members.has(e.source) && !members.has(e.target)) continue
    const [s, t] = flowEndpoints(e)
    if (!boxes.has(s) || !boxes.has(t)) continue
    if (members.has(s) && members.has(t)) {
      routes.set(e.id, orthogonalRoute(boxes.get(s), boxes.get(t)))
      continue
    }
    const { docked, other, key } = trunkOf(e)
    const obstacles = [...boxes].filter(([id]) => id !== s && id !== t).map(([, box]) => box)
    // Links both ways between one pair leave and enter apart, so neither reads as the other.
    const both = pairDirs.get(`${docked}|${other}`).size > 1
    const shift = both ? (key.endsWith(">out") ? -PORT_SHIFT : PORT_SHIFT) : 0
    // A node with several links one way sends them all down that trunk, so they share one run.
    const plain = linkCount.get(key) === 1 && !both
    if (!trunks.has(key)) trunks.set(key, [])
    let best = null
    for (const { side, gutter } of routeSides(boxes.get(other), rect)) {
      const lanedGutter = gutter + away[side] * lane.get(key) * LANE_STEP
      const found = scoredGutterRoute(
        boxes.get(docked),
        boxes.get(other),
        side,
        lanedGutter,
        obstacles,
        { plain, siblings: trunks.get(key), shift, exitClear: GROUP_PAD.side + GAP },
      )
      if (!best || found.score < best.score) best = found
    }
    const path = best.points
    trunks.get(key).push(path)
    routes.set(e.id, docked === s ? path : [...path].reverse())
  }
  // Labels are placed on the finished drawing (layout/labels.js).
  return { boxes, routes }
}

/** Clear space the docked links' labels need between the block and the drawing: [across x, across y]. */
function labelRoom(projection) {
  const members = new Set(projection.dock.memberIds)
  let w = 0
  let h = 0
  for (const e of projection.edges) {
    if (!e.layoutLabel || members.has(e.source) === members.has(e.target)) continue
    const size = labelSize(e.layoutLabel)
    w = Math.max(w, size.width)
    h = Math.max(h, size.height)
  }
  const lanes = LANES * LANE_STEP
  return w ? [w + 2 * LABEL_GAP + lanes, h + 2 * LABEL_GAP + lanes] : [0, 0]
}

/** Ways a link may leave a block placed in a free slot: every side, each with its gutter; the cheapest route wins. */
function slotSides(_target, rect) {
  const clear = GROUP_PAD.side + GAP
  return [
    { side: "left", gutter: rect.x + rect.width + clear },
    { side: "right", gutter: rect.x - clear },
    { side: "top", gutter: rect.y + rect.height + clear },
    { side: "bottom", gutter: rect.y - clear },
  ]
}

function rectsOverlap(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

/**
 * Up to `limit` free-slot placements: grid positions around and inside the
 * drawing where the block's backdrop clears every node, group and route,
 * ranked by the drawing's area fitted to `aspect`, its raw area, and the
 * distance to the nodes the block links to. Each is {name, placed}.
 */
export function slotDocks(base, projection, aspect, limit = 3) {
  const { ids, sizes } = dockMembers(projection)
  if (!ids.length) return []
  const b = bounds(base.boxes)
  const members = new Set(ids)
  const linked = projection.edges
    .filter((e) => members.has(e.source) !== members.has(e.target))
    .map((e) => base.boxes.get(members.has(e.source) ? e.target : e.source))
    .filter(Boolean)
  const routes = [...(base.routes || new Map()).values()]
  const groups = projection.groups.filter((g) => g.id !== projection.dock.groupId && !g.collapsed)
  const obstacles = [
    ...base.boxes.values(),
    ...groupBackdrops(groups, base.boxes).map((d) => ({
      x: d.x,
      y: d.y,
      width: d.width,
      height: d.height,
    })),
  ]
  const pad = GROUP_PAD
  const shapes = ["top", "left"].map((shape) =>
    packBlock(sizes, shape, shape === "top" ? Math.max(b.x1 - b.x0, 1) : Math.max(b.y1 - b.y0, 1)),
  )
  const step = 48
  const found = []
  // The zone keeps label room clear around the backdrop; only the backdrop counts as drawing extent.
  const [roomX, roomY] = labelRoom(projection)
  shapes.forEach((block, si) => {
    const inner = {
      w: block.width + 2 * pad.side + 2 * GAP,
      h: block.height + pad.top + pad.bottom + 2 * GAP,
    }
    const outer = { w: inner.w + 2 * roomX, h: inner.h + 2 * roomY }
    for (let y = b.y0 - outer.h; y <= b.y1; y += step) {
      for (let x = b.x0 - outer.w; x <= b.x1; x += step) {
        const zone = { x, y, width: outer.w, height: outer.h }
        if (obstacles.some((box) => rectsOverlap(zone, box))) continue
        const crossed = routes.some((pts) =>
          pts.some((p, i) => i > 0 && segmentHitsBox([pts[i - 1], p], zone, 0)),
        )
        if (crossed) continue
        const ix = x + roomX
        const iy = y + roomY
        const origin = { x: ix + pad.side + GAP, y: iy + pad.top + GAP }
        const w = Math.max(b.x1, ix + inner.w) - Math.min(b.x0, ix)
        const h = Math.max(b.y1, iy + inner.h) - Math.min(b.y0, iy)
        const fit = Math.max(w, h * aspect) * (Math.max(w, h * aspect) / aspect)
        const cx = origin.x + block.width / 2
        const cy = origin.y + block.height / 2
        const reach = linked.reduce(
          (s, t) => s + Math.abs(t.x + t.width / 2 - cx) + Math.abs(t.y + t.height / 2 - cy),
          0,
        )
        // Fitted area first; raw area breaks its ties (growing along the slack axis is still growth).
        found.push({ si, origin, score: fit + w * h * 0.25 + reach * 40 })
      }
    }
  })
  found.sort((a, c) => a.score - c.score)
  const picked = []
  for (const f of found) {
    if (picked.length >= limit) break
    // Keep the picks apart so they are real alternatives, not neighbouring grid cells.
    if (
      picked.some(
        (p) => Math.abs(p.origin.x - f.origin.x) + Math.abs(p.origin.y - f.origin.y) < step * 4,
      )
    )
      continue
    picked.push(f)
  }
  return picked.map((f, i) => ({
    name: `slot-${i}`,
    placed: placeBlock(base, projection, ids, sizes, shapes[f.si], f.origin, slotSides),
  }))
}

function hits(points, obstacles) {
  let n = 0
  for (let i = 1; i < points.length; i++)
    for (const box of obstacles) if (segmentHitsBox([points[i - 1], points[i]], box)) n += 1
  return n
}

function length(points) {
  let sum = 0
  for (let i = 1; i < points.length; i++)
    sum += Math.abs(points[i].x - points[i - 1].x) + Math.abs(points[i].y - points[i - 1].y)
  return sum
}

/**
 * Route from a docked box to a box in the drawing: out of the block into the
 * gutter line between block and drawing, along it, then into the target from
 * whichever side hits the fewest nodes, its own two ends included (ties:
 * shortest). With `plain`, the direct two-bend route competes too. `shift`
 * moves both ports along their sides; `exitClear` makes a link leaving the
 * block's top edge (`side` "bottom") leave sideways instead, past the
 * group header, by that clearance.
 */
export function gutterRoute(from, to, side, gutter, obstacles, options = {}) {
  return scoredGutterRoute(from, to, side, gutter, obstacles, options).points
}

function scoredGutterRoute(
  from,
  to,
  side,
  gutter,
  obstacles,
  { plain = true, siblings = [], shift = 0, exitClear = 0 } = {},
) {
  const vertical = side === "left" || side === "right"
  const cx = to.x + to.width / 2 + shift
  const cy = to.y + to.height / 2 + shift
  const head = headOf(from, side, cx, shift, exitClear)
  const tail = head[head.length - 1]
  const toGutter = vertical ? { x: gutter, y: tail.y } : { x: tail.x, y: gutter }
  const near = 12
  const entries = [
    { port: { x: to.x, y: cy }, approach: { x: to.x - near, y: cy } },
    { port: { x: to.x + to.width, y: cy }, approach: { x: to.x + to.width + near, y: cy } },
    { port: { x: cx, y: to.y }, approach: { x: cx, y: to.y - near } },
    { port: { x: cx, y: to.y + to.height }, approach: { x: cx, y: to.y + to.height + near } },
  ]
  const candidates = plain ? [orthogonalRoute(from, to)] : []
  for (const { port, approach } of entries) {
    const along = vertical ? { x: gutter, y: approach.y } : { x: approach.x, y: gutter }
    candidates.push(tidy([...head, toGutter, along, approach, port]))
  }
  // Drop off the gutter where a sibling link already runs, entering the target from that side.
  for (const lane of siblingLanes(siblings, vertical)) {
    if (vertical) {
      if (lane > to.y && lane < to.y + to.height) continue
      const py = lane < to.y ? to.y : to.y + to.height
      candidates.push(
        tidy([...head, toGutter, { x: gutter, y: lane }, { x: cx, y: lane }, { x: cx, y: py }]),
      )
    } else {
      if (lane > to.x && lane < to.x + to.width) continue
      const px = lane < to.x ? to.x : to.x + to.width
      candidates.push(
        tidy([...head, toGutter, { x: lane, y: gutter }, { x: lane, y: cy }, { x: px, y: cy }]),
      )
    }
  }
  const walls = [...obstacles, from, to]
  let best = null
  for (const points of candidates) {
    // Running on a sibling's line is a fan-in / fan-out, so most of it is free.
    const score =
      hits(points, walls) * 100000 + length(points) - shared(points, siblings) * SHARE_CREDIT
    if (!best || score < best.score) best = { points, score }
  }
  return best
}

/** The points a docked link takes from its node to where it turns into the gutter. */
function headOf(from, side, targetX, shift, exitClear) {
  const midX = from.x + from.width / 2
  const midY = from.y + from.height / 2
  if (side === "bottom" && exitClear) {
    const rightward = targetX >= midX
    const sx = rightward ? from.x + from.width : from.x
    const y = midY + shift
    return [
      { x: sx, y },
      { x: rightward ? sx + exitClear : sx - exitClear, y },
    ]
  }
  return [
    {
      left: { x: from.x + from.width, y: midY + shift },
      right: { x: from.x, y: midY + shift },
      top: { x: midX + shift, y: from.y + from.height },
      bottom: { x: midX + shift, y: from.y },
    }[side],
  ]
}

/** Rows (vertical gutter) or columns (horizontal gutter) where sibling links leave the gutter. */
function siblingLanes(siblings, vertical) {
  const lanes = new Set()
  for (const pts of siblings)
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1]
      const b = pts[i]
      if (vertical && Math.abs(a.y - b.y) < 0.5) lanes.add(a.y)
      if (!vertical && Math.abs(a.x - b.x) < 0.5) lanes.add(a.x)
    }
  return lanes
}

function shared(points, siblings) {
  let sum = 0
  for (const pts of siblings)
    for (let i = 1; i < points.length; i++)
      for (let j = 1; j < pts.length; j++)
        sum += sharedRun([points[i - 1], points[i]], [pts[j - 1], pts[j]])
  return sum
}

function tidy(points) {
  const out = []
  for (const p of points) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5) continue
    out.push({ x: p.x, y: p.y })
  }
  // Insert the corner a diagonal step would need, keeping every segment axis-aligned.
  const aligned = [out[0]]
  for (let i = 1; i < out.length; i++) {
    const a = aligned[aligned.length - 1]
    const p = out[i]
    if (Math.abs(a.x - p.x) > 0.5 && Math.abs(a.y - p.y) > 0.5) aligned.push({ x: p.x, y: a.y })
    aligned.push(p)
  }
  return aligned
}
