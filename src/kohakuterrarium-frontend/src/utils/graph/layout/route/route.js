/**
 * Orthogonal routing helpers: right-angle routes between two boxes, side
 * routes that enter a card across the flow, rounded
 * SVG paths for any polyline, and the label point halfway along a route.
 * Pure.
 */

import { segmentHitsBox } from "@/utils/graph/layout/metrics"

const CORNER = 8

function centre(box) {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v))
}

function dedupe(points) {
  const out = []
  for (const p of points) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5) continue
    out.push(p)
  }
  // Drop interior points that sit on a straight run.
  return out.filter((p, i) => {
    if (i === 0 || i === out.length - 1) return true
    const a = out[i - 1]
    const b = out[i + 1]
    return !(
      (Math.abs(a.x - p.x) < 0.5 && Math.abs(p.x - b.x) < 0.5) ||
      (Math.abs(a.y - p.y) < 0.5 && Math.abs(p.y - b.y) < 0.5)
    )
  })
}

/**
 * Right-angle route from `source` to `target`. The edge leaves the side that
 * faces the target along the dominant axis and turns twice at the midline.
 * `offset` shifts the attach points along the side so parallel edges separate.
 */
export function orthogonalRoute(source, target, offset = 0) {
  const s = centre(source)
  const t = centre(target)
  const dx = t.x - s.x
  const dy = t.y - s.y
  if (Math.abs(dx) >= Math.abs(dy)) {
    const sx = dx >= 0 ? source.x + source.width : source.x
    const tx = dx >= 0 ? target.x : target.x + target.width
    const sy = clamp(s.y + offset, source.y + 6, source.y + source.height - 6)
    const ty = clamp(t.y + offset, target.y + 6, target.y + target.height - 6)
    const mid = (sx + tx) / 2
    return dedupe([
      { x: sx, y: sy },
      { x: mid, y: sy },
      { x: mid, y: ty },
      { x: tx, y: ty },
    ])
  }
  const sy = dy >= 0 ? source.y + source.height : source.y
  const ty = dy >= 0 ? target.y : target.y + target.height
  const sx = clamp(s.x + offset, source.x + 10, source.x + source.width - 10)
  const tx = clamp(t.x + offset, target.x + 10, target.x + target.width - 10)
  const mid = (sy + ty) / 2
  return dedupe([
    { x: sx, y: sy },
    { x: sx, y: mid },
    { x: tx, y: mid },
    { x: tx, y: ty },
  ])
}

const SIDE_LANE = 18
const SIDE_STUB = 12
const SIDE_GAP = 10
// Where a side link meets the card, as a fraction of the side: below the
// left input port, between the right send and wire ports, mid top / bottom.
const SIDE_PORT = { down: [0.8, 0.5], right: [0.5, 0.5] }

const transpose = (b) => ({ x: b.y, y: b.x, width: b.height, height: b.width })

function hitCount(points, walls) {
  let n = 0
  for (let i = 1; i < points.length; i++)
    for (const w of walls) if (segmentHitsBox([points[i - 1], points[i]], w)) n += 1
  return n
}

/** `sideRoute` for a downward flow: into the left or right side of `target`. */
function sideRouteDown(source, target, walls, [nearPort, farPort]) {
  const s = centre(source)
  const t = centre(target)
  const left = s.x <= t.x
  const tx = left ? target.x : target.x + target.width
  const ty = target.y + target.height * (left ? nearPort : farPort)
  const lane = left ? tx - SIDE_LANE : tx + SIDE_LANE
  const dir = lane >= s.x ? 1 : -1
  const sx = dir > 0 ? source.x + source.width : source.x
  const stub = sx + dir * SIDE_STUB
  const lo = Math.min(stub, lane)
  const hi = Math.max(stub, lane)
  // The run goes at the source's middle, or just past a card it would cross.
  const runs = new Set([s.y])
  for (const w of walls)
    if (w.x < hi && w.x + w.width > lo) runs.add(w.y - SIDE_GAP).add(w.y + w.height + SIDE_GAP)
  let best = null
  for (const y of runs) {
    const inside = y >= source.y + 6 && y <= source.y + source.height - 6
    const head = inside
      ? [{ x: sx, y }]
      : [
          { x: sx, y: s.y },
          { x: stub, y: s.y },
          { x: stub, y },
        ]
    const points = dedupe([...head, { x: lane, y }, { x: lane, y: ty }, { x: tx, y: ty }])
    const length = points.reduce(
      (sum, p, i) =>
        i ? sum + Math.abs(p.x - points[i - 1].x) + Math.abs(p.y - points[i - 1].y) : 0,
      0,
    )
    const score = hitCount(points, walls) * 100000 + length
    if (!best || score < best.score) best = { points, score }
  }
  return best.points
}

/**
 * Right-angle route into the side of `target` across the flow `axis`
 * ("down": the left / right side, "right": the top / bottom side), the side
 * facing `source`. It ends in a lane just outside that side, so it never
 * enters where the flow does, and picks the run that crosses the fewest of
 * `obstacles` (boxes; the two ends may be among them), then the shortest.
 */
export function sideRoute(source, target, axis, obstacles = []) {
  if (axis === "down") return sideRouteDown(source, target, obstacles, SIDE_PORT.down)
  const points = sideRouteDown(
    transpose(source),
    transpose(target),
    obstacles.map(transpose),
    SIDE_PORT.right,
  )
  return points.map((p) => ({ x: p.y, y: p.x }))
}

/** SVG path through `points` with rounded corners. */
export function roundedPath(points, radius = CORNER) {
  if (!points?.length) return ""
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`
  let d = `M ${points[0].x} ${points[0].y}`
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i]
    const a = points[i - 1]
    const b = points[i + 1]
    const inLen = Math.hypot(p.x - a.x, p.y - a.y)
    const outLen = Math.hypot(b.x - p.x, b.y - p.y)
    const r = Math.min(radius, inLen / 2, outLen / 2)
    const p1 = {
      x: p.x - ((p.x - a.x) / (inLen || 1)) * r,
      y: p.y - ((p.y - a.y) / (inLen || 1)) * r,
    }
    const p2 = {
      x: p.x + ((b.x - p.x) / (outLen || 1)) * r,
      y: p.y + ((b.y - p.y) / (outLen || 1)) * r,
    }
    d += ` L ${p1.x} ${p1.y} Q ${p.x} ${p.y} ${p2.x} ${p2.y}`
  }
  const last = points[points.length - 1]
  return `${d} L ${last.x} ${last.y}`
}

/** Point halfway along the polyline's length. */
export function routeMidpoint(points) {
  if (!points?.length) return { x: 0, y: 0 }
  let total = 0
  for (let i = 1; i < points.length; i++)
    total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y)
  let walked = 0
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]
    const b = points[i]
    const seg = Math.hypot(b.x - a.x, b.y - a.y)
    if (walked + seg >= total / 2) {
      const f = seg ? (total / 2 - walked) / seg : 0
      return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f }
    }
    walked += seg
  }
  return points[points.length - 1]
}
