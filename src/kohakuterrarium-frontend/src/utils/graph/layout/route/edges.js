/**
 * Edge presentation helpers shared by the canvas views. Pure.
 */

import { neighbourhoodOf } from "@/utils/graph/data/projection"

const BEND_STEP = 32
const WIRE_BOW = 34

function centre(box) {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/**
 * Sideways bow (px, same convention as `floatingPath`) per edge:
 * - several edges between the same two nodes fan out instead of overlapping;
 * - output wires bow away from the graph's centre, where channels sit, so a
 *   wire does not cut through the channel pills between creatures.
 * `boxes` (id → box) is optional; without it wires are not bowed.
 */
export function edgeBends(edges, boxes = null) {
  const groups = new Map()
  for (const e of edges) {
    const [a, b] = e.source < e.target ? [e.source, e.target] : [e.target, e.source]
    const key = `${a}|${b}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(e)
  }
  const bends = new Map()
  for (const list of groups.values()) {
    if (list.length < 2) continue
    list.forEach((e, k) => {
      const offset = (k - (list.length - 1) / 2) * BEND_STEP
      bends.set(e.id, e.source < e.target ? offset : -offset)
    })
  }
  if (!boxes || !boxes.size) return bends
  const all = [...boxes.values()].map(centre)
  const cx = all.reduce((s, p) => s + p.x, 0) / all.length
  const cy = all.reduce((s, p) => s + p.y, 0) / all.length
  for (const e of edges) {
    if (e.kind !== "wire" || !boxes.has(e.source) || !boxes.has(e.target)) continue
    const s = centre(boxes.get(e.source))
    const t = centre(boxes.get(e.target))
    const len = Math.hypot(t.x - s.x, t.y - s.y) || 1
    const nx = -(t.y - s.y) / len
    const ny = (t.x - s.x) / len
    const outward = nx * ((s.x + t.x) / 2 - cx) + ny * ((s.y + t.y) / 2 - cy) >= 0 ? 1 : -1
    bends.set(e.id, (bends.get(e.id) || 0) + outward * WIRE_BOW)
  }
  return bends
}

/** Ids lit while hovering `id`: the node, its edges, and its neighbours (one channel hop). */
export function hoverNeighbourhood(id, projection) {
  return neighbourhoodOf(id, projection.nodes, projection.edges)
}
