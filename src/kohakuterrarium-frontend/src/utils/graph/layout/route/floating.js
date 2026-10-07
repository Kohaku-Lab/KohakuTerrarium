/**
 * Floating-edge geometry: an edge leaves and enters a node where the line
 * toward the other end crosses the node's border, so edges never loop
 * around a node to reach a fixed port. Pure.
 */

const GAP = 3

/** Absolute box of a Vue Flow graph node. */
export function nodeBox(node) {
  const p = node?.computedPosition || node?.position || { x: 0, y: 0 }
  const d = node?.dimensions || { width: 0, height: 0 }
  return { x: p.x, y: p.y, width: d.width, height: d.height }
}

function center(box) {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** Point where the ray from the box centre toward `toward` leaves the box (plus a small gap). */
export function borderPoint(box, toward) {
  const c = center(box)
  const dx = toward.x - c.x
  const dy = toward.y - c.y
  if (dx === 0 && dy === 0) return c
  const hw = box.width / 2 + GAP
  const hh = box.height / 2 + GAP
  const scale = Math.min(
    dx === 0 ? Infinity : hw / Math.abs(dx),
    dy === 0 ? Infinity : hh / Math.abs(dy),
  )
  return { x: c.x + dx * scale, y: c.y + dy * scale }
}

/**
 * SVG path between two boxes. `bend` (px) bows the edge sideways so a pair
 * of opposite edges between the same nodes do not overlap.
 */
export function floatingPath(source, target, bend = 0) {
  const sc = center(source)
  const tc = center(target)
  const mx = (sc.x + tc.x) / 2
  const my = (sc.y + tc.y) / 2
  const len = Math.hypot(tc.x - sc.x, tc.y - sc.y) || 1
  const nx = -(tc.y - sc.y) / len
  const ny = (tc.x - sc.x) / len
  const control = { x: mx + nx * bend * 2, y: my + ny * bend * 2 }
  const s = borderPoint(source, bend ? control : tc)
  const t = borderPoint(target, bend ? control : sc)
  if (!bend) {
    return {
      path: `M ${s.x} ${s.y} L ${t.x} ${t.y}`,
      labelX: (s.x + t.x) / 2,
      labelY: (s.y + t.y) / 2,
    }
  }
  const labelX = 0.25 * s.x + 0.5 * control.x + 0.25 * t.x
  const labelY = 0.25 * s.y + 0.5 * control.y + 0.25 * t.y
  return { path: `M ${s.x} ${s.y} Q ${control.x} ${control.y} ${t.x} ${t.y}`, labelX, labelY }
}
