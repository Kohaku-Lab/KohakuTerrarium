/**
 * Orthogonal routing helpers: right-angle routes between two boxes, rounded
 * SVG paths for any polyline, and the label point halfway along a route.
 * Pure.
 */

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
