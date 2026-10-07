/**
 * Layout quality metrics, the place-and-route cost of a drawing: edge
 * crossings, unrelated edges running on top of each other (allowed only as a
 * fan-out from one source or a fan-in to one target), edges running through
 * nodes, node overlap, total wire length,
 * and the area the drawing occupies once fitted to the viewport. With a
 * viewport aspect (width / height) that area is the smallest viewport-shaped
 * box around the drawing, so it scores size and aspect match together; a
 * small raw-area term keeps growth along the slack axis from being free.
 * Lower is better on every axis. Pure.
 */

export const COST_WEIGHTS = Object.freeze({
  crossing: 600,
  // Per pixel two unrelated connections run on top of each other (150 px ≈ one crossing).
  merged: 4,
  nodeHit: 900,
  overlap: 0.5,
  length: 1,
  area: 0.004,
  rawArea: 0.001,
})

function segmentsOf(points) {
  const out = []
  for (let i = 1; i < points.length; i++) out.push([points[i - 1], points[i]])
  return out
}

function orient(a, b, c) {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
}

function near(a, b) {
  return Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) < 1
}

/** Proper crossing of two segments; touching at a shared endpoint does not count. */
export function segmentsCross([a, b], [c, d]) {
  if (near(a, c) || near(a, d) || near(b, c) || near(b, d)) return false
  const o1 = orient(a, b, c)
  const o2 = orient(a, b, d)
  const o3 = orient(c, d, a)
  const o4 = orient(c, d, b)
  return o1 * o2 < 0 && o3 * o4 < 0
}

/** Whether a segment passes through the inside of `box` (shrunk slightly). */
export function segmentHitsBox([a, b], box, inset = 3) {
  const x0 = box.x + inset
  const y0 = box.y + inset
  const x1 = box.x + box.width - inset
  const y1 = box.y + box.height - inset
  if (x1 <= x0 || y1 <= y0) return false
  // Liang–Barsky clip against the rectangle.
  let t0 = 0
  let t1 = 1
  const dx = b.x - a.x
  const dy = b.y - a.y
  const checks = [
    [-dx, a.x - x0],
    [dx, x1 - a.x],
    [-dy, a.y - y0],
    [dy, y1 - a.y],
  ]
  for (const [p, q] of checks) {
    if (p === 0) {
      if (q < 0) return false
      continue
    }
    const r = q / p
    if (p < 0) t0 = Math.max(t0, r)
    else t1 = Math.min(t1, r)
    if (t0 > t1) return false
  }
  return t1 - t0 > 1e-6
}

const CELL = 160

function cellKeys(x0, y0, x1, y1) {
  const keys = []
  const cx0 = Math.floor(Math.min(x0, x1) / CELL)
  const cx1 = Math.floor(Math.max(x0, x1) / CELL)
  const cy0 = Math.floor(Math.min(y0, y1) / CELL)
  const cy1 = Math.floor(Math.max(y0, y1) / CELL)
  for (let cx = cx0; cx <= cx1; cx++) for (let cy = cy0; cy <= cy1; cy++) keys.push(`${cx},${cy}`)
  return keys
}

/** Grid cell key → indices of the rectangles ([x0, y0, x1, y1]) that touch it. */
function bucket(rects) {
  const grid = new Map()
  rects.forEach(([x0, y0, x1, y1], i) => {
    for (const key of cellKeys(x0, y0, x1, y1)) {
      if (!grid.has(key)) grid.set(key, [])
      grid.get(key).push(i)
    }
  })
  return grid
}

function overlapArea(a, b) {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

/**
 * Length two segments run on top of each other: both horizontal on the same
 * row or both vertical on the same column, over their common span.
 */
export function sharedRun([a, b], [c, d]) {
  const horizontal = (p, q) => Math.abs(p.y - q.y) < 0.5
  if (horizontal(a, b) && horizontal(c, d) && Math.abs(a.y - c.y) < 0.5) {
    const lo = Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x))
    const hi = Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x))
    return Math.max(0, hi - lo)
  }
  const vertical = (p, q) => Math.abs(p.x - q.x) < 0.5
  if (vertical(a, b) && vertical(c, d) && Math.abs(a.x - c.x) < 0.5) {
    const lo = Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y))
    const hi = Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y))
    return Math.max(0, hi - lo)
  }
  return 0
}

/** Area of the smallest box with aspect `aspect` (width / height) containing a width × height drawing. */
export function fittedArea(width, height, aspect) {
  if (!aspect || !(width > 0) || !(height > 0)) return width * height
  const w = Math.max(width, height * aspect)
  return w * (w / aspect)
}

/**
 * Drawn length of `segments`: colinear axis-aligned runs on the same row or
 * column count once where they overlap; other segments count in full.
 */
export function inkLength(segments) {
  const lines = new Map()
  let other = 0
  for (const [a, b] of segments) {
    const h = Math.abs(a.y - b.y) < 0.5
    const v = Math.abs(a.x - b.x) < 0.5
    if (!h && !v) {
      other += Math.hypot(b.x - a.x, b.y - a.y)
      continue
    }
    const key = h ? `h${Math.round(a.y)}` : `v${Math.round(a.x)}`
    const lo = h ? Math.min(a.x, b.x) : Math.min(a.y, b.y)
    const hi = h ? Math.max(a.x, b.x) : Math.max(a.y, b.y)
    if (!lines.has(key)) lines.set(key, [])
    lines.get(key).push([lo, hi])
  }
  let sum = other
  for (const spans of lines.values()) {
    spans.sort((p, q) => p[0] - q[0])
    let [lo, hi] = spans[0]
    for (const [s, e] of spans.slice(1)) {
      if (s > hi) {
        sum += hi - lo
        lo = s
      }
      hi = Math.max(hi, e)
    }
    sum += hi - lo
  }
  return sum
}

/**
 * Measure a drawing. `boxes`: Map id → {x, y, width, height};
 * `routes`: array of {id, source, target, points}; `aspect`: viewport width / height;
 * `labelHits`: label collisions (see layout/labels.js), weighed like an edge through a node.
 */
export function measureLayout(boxes, routes, { aspect = null, labelHits = 0 } = {}) {
  const list = [...boxes.entries()]
  let overlap = 0
  for (let i = 0; i < list.length; i++)
    for (let j = i + 1; j < list.length; j++) overlap += overlapArea(list[i][1], list[j][1])

  let length = 0
  const segs = []
  for (const r of routes) {
    for (const s of segmentsOf(r.points)) {
      length += Math.hypot(s[1].x - s[0].x, s[1].y - s[0].y)
      segs.push({ route: r, s })
    }
  }

  // Pairs are only compared within shared grid cells; each pair is counted once.
  const segGrid = bucket(segs.map(({ s: [a, b] }) => [a.x, a.y, b.x, b.y]))
  let crossings = 0
  let merged = 0
  const seen = new Set()
  for (const ids of segGrid.values()) {
    for (let x = 0; x < ids.length; x++) {
      for (let y = x + 1; y < ids.length; y++) {
        const i = Math.min(ids[x], ids[y])
        const j = Math.max(ids[x], ids[y])
        const key = i * segs.length + j
        if (seen.has(key)) continue
        seen.add(key)
        const ri = segs[i].route
        const rj = segs[j].route
        if (ri === rj) continue
        if (segmentsCross(segs[i].s, segs[j].s)) crossings += 1
        // A bundle is fine only as a fan-out (same source) or a fan-in (same target).
        if (ri.source !== rj.source && ri.target !== rj.target)
          merged += sharedRun(segs[i].s, segs[j].s)
      }
    }
  }

  const boxGrid = bucket(list.map(([, b]) => [b.x, b.y, b.x + b.width, b.y + b.height]))
  let nodeHits = 0
  for (const { route, s } of segs) {
    const near = new Set()
    for (const key of cellKeys(s[0].x, s[0].y, s[1].x, s[1].y))
      for (const k of boxGrid.get(key) || []) near.add(k)
    for (const k of near) {
      const [id, box] = list[k]
      if (id === route.source || id === route.target) continue
      if (segmentHitsBox(s, box)) nodeHits += 1
    }
  }

  const xs = list.flatMap(([, b]) => [b.x, b.x + b.width])
  const ys = list.flatMap(([, b]) => [b.y, b.y + b.height])
  const width = list.length ? Math.max(...xs) - Math.min(...xs) : 0
  const height = list.length ? Math.max(...ys) - Math.min(...ys) : 0
  const area = width * height
  const fitArea = fittedArea(width, height, aspect)

  const ink = inkLength(segs.map(({ s }) => s))
  const w = COST_WEIGHTS
  // Wire cost is ink: a run several edges share (a fan-in / fan-out trunk) is drawn once.
  const cost =
    crossings * w.crossing +
    merged * w.merged +
    nodeHits * w.nodeHit +
    labelHits * w.nodeHit +
    overlap * w.overlap +
    ink * w.length +
    fitArea * w.area +
    area * w.rawArea
  return {
    crossings,
    merged: Math.round(merged),
    nodeHits,
    labelHits,
    overlap,
    length: Math.round(length),
    ink: Math.round(ink),
    width: Math.round(width),
    height: Math.round(height),
    area: Math.round(area),
    fitArea: Math.round(fitArea),
    aspect: height ? Math.round((width / height) * 100) / 100 : 0,
    cost: Math.round(cost),
  }
}
