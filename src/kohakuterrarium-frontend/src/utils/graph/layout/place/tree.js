/**
 * Tree placements for the spawn tree. Every placement returns boxes and
 * tree connectors: a stem down from the parent to one bar shared by its
 * children, then a drop to each child.
 *
 * - `stackedTree`: tidy tree where a parent's leaf children (at least
 *   `threshold` of them) hang off a vertical spine instead of a wide row, in
 *   one column beside it or two columns either side (an org-chart compaction).
 * - `treePlacement`: positions from ELK (tidy tree or layered), bracket routes.
 */

import { getElk, sizeOf } from "@/utils/graph/layout/place/elk"

const GAP_X = 32
const GAP_Y = 48
const STACK_GAP = 16
const SPINE_PAD = 24

function tidy(points) {
  const out = []
  for (const p of points) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5) continue
    const prev = out[out.length - 2]
    if (prev && last) {
      const sameX = Math.abs(prev.x - last.x) < 0.5 && Math.abs(last.x - p.x) < 0.5
      const sameY = Math.abs(prev.y - last.y) < 0.5 && Math.abs(last.y - p.y) < 0.5
      if (sameX || sameY) out.pop()
    }
    out.push({ x: p.x, y: p.y })
  }
  return out
}

/** Bracket routes: every child of a parent shares the bar halfway to its nearest child. */
export function bracketRoutes(edges, boxes) {
  const routes = new Map()
  const bySource = new Map()
  for (const e of edges) {
    if (!boxes.has(e.source) || !boxes.has(e.target)) continue
    if (!bySource.has(e.source)) bySource.set(e.source, [])
    bySource.get(e.source).push(e)
  }
  for (const [source, list] of bySource) {
    const s = boxes.get(source)
    const sx = s.x + s.width / 2
    const sy = s.y + s.height
    const nearest = Math.min(...list.map((e) => boxes.get(e.target).y))
    const bar = sy + (nearest - sy) / 2
    for (const e of list) {
      const t = boxes.get(e.target)
      const tx = t.x + t.width / 2
      routes.set(
        e.id,
        tidy([
          { x: sx, y: sy },
          { x: sx, y: bar },
          { x: tx, y: bar },
          { x: tx, y: t.y },
        ]),
      )
    }
  }
  return routes
}

/**
 * Tidy tree with stacked leaves. `cols` 1 or 2 columns per stack; leaf
 * children are stacked when there are at least `threshold` of them.
 * Resolves to {boxes, routes}; edges must form a tree.
 */
export function stackedTree(projection, { cols = 2, threshold = 3 } = {}) {
  const byId = new Map(projection.nodes.map((n) => [n.id, n]))
  const kids = new Map(projection.nodes.map((n) => [n.id, []]))
  const edgeOf = new Map()
  const hasParent = new Set()
  for (const e of projection.edges) {
    if (!kids.has(e.source) || !kids.has(e.target) || hasParent.has(e.target)) continue
    kids.get(e.source).push(e.target)
    edgeOf.set(`${e.source}>${e.target}`, e.id)
    hasParent.add(e.target)
  }
  const boxes = new Map()
  const routes = new Map()

  const stackItem = (leaves) => {
    const sizes = leaves.map((id) => sizeOf(byId.get(id)))
    const lw = Math.max(...sizes.map((s) => s.width))
    const lh = Math.max(...sizes.map((s) => s.height))
    const c = Math.min(cols, leaves.length)
    const rows = Math.ceil(leaves.length / c)
    const w = c === 1 ? SPINE_PAD + lw : 2 * lw + 2 * SPINE_PAD
    const h = rows * (lh + STACK_GAP) - STACK_GAP
    return {
      w,
      h,
      place(x, y) {
        this.spineX = c === 1 ? x + SPINE_PAD / 2 : x + lw + SPINE_PAD
        this.leaves = leaves.map((id, i) => {
          const col = c === 1 ? 1 : i % 2
          const row = c === 1 ? i : Math.floor(i / 2)
          const s = sizeOf(byId.get(id))
          const bx = c === 1 ? x + SPINE_PAD : col === 0 ? x : x + lw + 2 * SPINE_PAD
          const by = y + row * (lh + STACK_GAP)
          boxes.set(id, { x: bx, y: by, width: s.width, height: s.height })
          return { id, edgeX: col === 0 ? bx + s.width : bx, midY: by + s.height / 2 }
        })
      },
    }
  }

  const build = (id) => {
    const size = sizeOf(byId.get(id))
    const children = kids.get(id)
    const leaves = children.filter((k) => !kids.get(k).length)
    const stacked = leaves.length >= threshold ? leaves : []
    const items = []
    if (stacked.length) items.push({ ...stackItem(stacked), stack: true })
    for (const k of children) if (!stacked.includes(k)) items.push({ ...build(k), childId: k })
    const rowW = items.reduce((s, it) => s + it.w, 0) + GAP_X * Math.max(0, items.length - 1)
    const rowH = Math.max(0, ...items.map((it) => it.h))
    const w = Math.max(size.width, rowW)
    return {
      w,
      h: size.height + (items.length ? GAP_Y + rowH : 0),
      place(x, y) {
        const box = { x: x + (w - size.width) / 2, y, width: size.width, height: size.height }
        boxes.set(id, box)
        const cx = box.x + box.width / 2
        const bottom = y + size.height
        const rowY = bottom + GAP_Y
        const bar = bottom + GAP_Y / 2
        let ix = x + (w - rowW) / 2
        for (const it of items) {
          it.place(ix, rowY)
          if (it.stack) {
            for (const leaf of it.leaves)
              routes.set(
                edgeOf.get(`${id}>${leaf.id}`),
                tidy([
                  { x: cx, y: bottom },
                  { x: cx, y: bar },
                  { x: it.spineX, y: bar },
                  { x: it.spineX, y: leaf.midY },
                  { x: leaf.edgeX, y: leaf.midY },
                ]),
              )
          } else {
            const child = boxes.get(it.childId)
            const tx = child.x + child.width / 2
            routes.set(
              edgeOf.get(`${id}>${it.childId}`),
              tidy([
                { x: cx, y: bottom },
                { x: cx, y: bar },
                { x: tx, y: bar },
                { x: tx, y: child.y },
              ]),
            )
          }
          ix += it.w + GAP_X
        }
      },
    }
  }

  let x = 0
  for (const n of projection.nodes) {
    if (hasParent.has(n.id)) continue
    const tree = build(n.id)
    tree.place(x, 0)
    x += tree.w + GAP_X * 2
  }
  return { boxes, routes }
}

const ALGORITHM_OPTIONS = {
  mrtree: {
    "elk.algorithm": "mrtree",
    "elk.spacing.nodeNode": "36",
    "elk.mrtree.weighting": "CONSTRAINT",
  },
  layered: {
    "elk.algorithm": "layered",
    "elk.layered.spacing.nodeNodeBetweenLayers": "56",
    "elk.spacing.nodeNode": "36",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  },
}

/** ELK tree candidate; `algorithm` is "mrtree" or "layered". Resolves to {boxes, routes}. */
export async function treePlacement(projection, { aspect, algorithm = "mrtree" } = {}) {
  const elk = await getElk()
  const ids = new Set(projection.nodes.map((n) => n.id))
  const edges = projection.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
  const result = await elk.layout({
    id: "root",
    layoutOptions: {
      ...ALGORITHM_OPTIONS[algorithm],
      "elk.direction": "DOWN",
      ...(aspect ? { "elk.aspectRatio": String(aspect) } : {}),
    },
    children: projection.nodes.map((n) => ({ id: n.id, ...sizeOf(n) })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  })
  const boxes = new Map(
    result.children.map((c) => [
      c.id,
      { x: c.x || 0, y: c.y || 0, width: c.width, height: c.height },
    ]),
  )
  return { boxes, routes: bracketRoutes(edges, boxes) }
}
