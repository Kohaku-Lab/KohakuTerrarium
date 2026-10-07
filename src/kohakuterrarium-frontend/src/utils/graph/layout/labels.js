/**
 * Label placement for a finished drawing. Every label sits on or flush
 * beside its own line, so it reads as belonging to that line alone, and
 * covers no other line, node (with its ports), arrowhead, group header or
 * label. Labels that cannot avoid a collision are counted, so layout
 * candidates compete on how cleanly they label. Pure.
 */

import { segmentHitsBox } from "@/utils/graph/layout/metrics"
import { GROUP_PAD, flowEndpoints, groupBackdrops, labelSize } from "@/utils/graph/layout/place/elk"

/** Port glyphs and selection rings reach this far outside a node's box. */
export const NODE_MARGIN = 8
/** Half the side of the square an arrowhead occupies at a route's end. */
export const ARROW_HALF = 9
const STEP = 8
const HIT = 1000

function rectAt(center, size) {
  return {
    x: center.x - size.width / 2,
    y: center.y - size.height / 2,
    width: size.width,
    height: size.height,
  }
}

function inflate(box, by) {
  return { x: box.x - by, y: box.y - by, width: box.width + 2 * by, height: box.height + 2 * by }
}

function rectsOverlap(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

function pathLength(path) {
  let sum = 0
  for (let i = 1; i < path.length; i++)
    sum += Math.abs(path[i].x - path[i - 1].x) + Math.abs(path[i].y - path[i - 1].y)
  return sum
}

/**
 * Candidate centres along `path`: on the line, and flush beside it on both
 * sides. `anchor` is the distance along the path the label prefers.
 */
function candidates(path, size, anchor) {
  const out = []
  let walked = 0
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1]
    const b = path[i]
    const len = Math.abs(b.x - a.x) + Math.abs(b.y - a.y)
    const horizontal = Math.abs(b.y - a.y) < 0.5
    const dx = Math.sign(b.x - a.x)
    const dy = Math.sign(b.y - a.y)
    // Beside a line the label's edge sits on it, so the two still touch.
    const side = horizontal ? size.height / 2 : size.width / 2
    for (let d = 0; d <= len; d += STEP) {
      const p = { x: a.x + dx * d, y: a.y + dy * d }
      const cost = Math.abs(walked + d - anchor)
      out.push({ center: p, cost })
      const offsets = horizontal
        ? [
            { x: 0, y: -side },
            { x: 0, y: side },
          ]
        : [
            { x: -side, y: 0 },
            { x: side, y: 0 },
          ]
      for (const o of offsets) out.push({ center: { x: p.x + o.x, y: p.y + o.y }, cost: cost + 6 })
    }
    walked += len
  }
  return out
}

/** Uniform bucket grid: `near(rect)` yields each entry whose bounds share a cell with `rect`, once. */
class Grid {
  constructor(cell = 160) {
    this.cell = cell
    this.cells = new Map()
  }

  *keys(r) {
    const c = this.cell
    for (let x = Math.floor(r.x / c); x <= Math.floor((r.x + r.width) / c); x++)
      for (let y = Math.floor(r.y / c); y <= Math.floor((r.y + r.height) / c); y++)
        yield `${x},${y}`
  }

  add(entry, bounds) {
    for (const k of this.keys(bounds)) {
      if (!this.cells.has(k)) this.cells.set(k, [])
      this.cells.get(k).push(entry)
    }
  }

  near(rect) {
    const seen = new Set()
    for (const k of this.keys(rect)) for (const e of this.cells.get(k) || []) seen.add(e)
    return seen
  }
}

function segBounds([a, b]) {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(b.x - a.x),
    height: Math.abs(b.y - a.y),
  }
}

/**
 * The things a label must not cover: node boxes (inflated by NODE_MARGIN),
 * arrowheads at both ends of every route, group headers and borders, and
 * every route's segments (tagged with the route id, skipped for the label's
 * own lines). Returns a Grid of {kind: "box" | "seg", rect | seg, id?}.
 */
export function labelObstacles(boxes, routes, groups) {
  const grid = new Grid()
  const box = (rect) => grid.add({ kind: "box", rect }, rect)
  const seg = (s, id = null) => grid.add({ kind: "seg", seg: s, id }, segBounds(s))
  for (const b of boxes.values()) box(inflate(b, NODE_MARGIN))
  for (const [id, pts] of routes) {
    for (let i = 1; i < pts.length; i++) seg([pts[i - 1], pts[i]], id)
    for (const p of [pts[0], pts[pts.length - 1]])
      box({
        x: p.x - ARROW_HALF,
        y: p.y - ARROW_HALF,
        width: 2 * ARROW_HALF,
        height: 2 * ARROW_HALF,
      })
  }
  for (const d of groupBackdrops(groups, boxes)) {
    box({ x: d.x, y: d.y, width: d.width, height: GROUP_PAD.top })
    const c = [
      { x: d.x, y: d.y },
      { x: d.x + d.width, y: d.y },
      { x: d.x + d.width, y: d.y + d.height },
      { x: d.x, y: d.y + d.height },
    ]
    for (let i = 0; i < 4; i++) seg([c[i], c[(i + 1) % 4]])
  }
  return grid
}

/** Collisions of a label rect: other lines, boxes and placed labels it covers. */
function collisions(grid, rect, own) {
  let n = 0
  for (const o of grid.near(rect)) {
    if (o.kind === "seg") {
      if (!own.has(o.id) && segmentHitsBox(o.seg, rect, 1)) n += 1
    } else if (rectsOverlap(rect, o.rect)) n += 1
  }
  return n
}

/**
 * Place `items` ({id, size, path, anchor, own}: `path` the route to sit on,
 * `anchor` the preferred distance along it, `own` the route ids the label
 * may cover) against `grid`. Placed labels join the grid. Returns
 * {labels: Map id → centre, hits}.
 */
export function placeLabels(items, grid) {
  const labels = new Map()
  let hits = 0
  // Labels with the least room choose first.
  const order = [...items].sort((a, b) => pathLength(a.path) - pathLength(b.path))
  for (const item of order) {
    let best = null
    for (const c of candidates(item.path, item.size, item.anchor)) {
      const rect = rectAt(c.center, item.size)
      const score = collisions(grid, rect, item.own) * HIT + c.cost
      if (!best || score < best.score) best = { score, center: c.center, rect }
    }
    if (!best) continue
    labels.set(item.id, best.center)
    hits += Math.floor(best.score / HIT)
    grid.add({ kind: "box", rect: best.rect }, best.rect)
  }
  return { labels, hits }
}

/**
 * Label every labelled edge of `projection` on the drawing `placed`
 * ({boxes, routes}), plus its `bundles` ({id, text, members, end}: one
 * label for links sharing a trunk into `end`). A link touching the docked
 * group is labelled near its far end, a bundle near `end`, the rest near
 * the middle. Returns {labels: Map id → centre, hits}.
 */
export function labelDrawing(projection, placed) {
  const routes = placed.routes || new Map()
  const groups = projection.groups.filter((g) => !g.collapsed)
  const grid = labelObstacles(placed.boxes, routes, groups)
  const docked = new Set(projection.dock?.memberIds || [])
  const items = []
  for (const e of projection.edges) {
    const pts = routes.get(e.id)
    if (!e.layoutLabel || !pts || pts.length < 2) continue
    const size = labelSize(e.layoutLabel)
    const [s, t] = flowEndpoints(e)
    if (docked.has(s) !== docked.has(t)) {
      const path = docked.has(s) ? [...pts].reverse() : pts
      items.push({ id: e.id, size, path, anchor: 0, own: new Set([e.id]) })
    } else
      items.push({ id: e.id, size, path: pts, anchor: pathLength(pts) / 2, own: new Set([e.id]) })
  }
  for (const b of projection.bundles || []) {
    const member = b.members.find((id) => routes.has(id))
    if (!member) continue
    const pts = routes.get(member)
    const e = projection.edges.find((x) => x.id === member)
    const startsAtEnd = flowEndpoints(e)[0] === b.end
    items.push({
      id: b.id,
      size: labelSize(b.text),
      path: startsAtEnd ? pts : [...pts].reverse(),
      anchor: 0,
      own: new Set(b.members),
    })
  }
  return placeLabels(items, grid)
}
