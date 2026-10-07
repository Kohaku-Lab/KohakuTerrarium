/**
 * Network placement: creatures that share channels or wires settle close
 * (stress layout on a creature-only skeleton, one block per group, blocks
 * shelf-packed towards the viewport aspect), channels sit at the centre of their members, and an
 * overlap-removal pass separates boxes. Edges get right-angle routes.
 */

import { getElk, sizeOf, flowEndpoints, NODE_SIZE, GROUP_PAD } from "@/utils/graph/layout/place/elk"
import { orthogonalRoute } from "@/utils/graph/layout/route/route"

const BLOCK_GAP = 120

/** Creature-only link list: two actors are linked when they share a channel or a wire. */
export function creatureSkeleton(projection) {
  const actors = projection.nodes.filter((n) => n.kind !== "channel")
  const ids = new Set(actors.map((n) => n.id))
  const membersOf = new Map()
  const links = new Set()
  for (const e of projection.edges) {
    if (e.kind === "channel") {
      if (!membersOf.has(e.target)) membersOf.set(e.target, new Set())
      membersOf.get(e.target).add(e.source)
    } else if (ids.has(e.source) && ids.has(e.target) && e.source !== e.target) {
      links.add([e.source, e.target].sort().join("|"))
    }
  }
  for (const members of membersOf.values()) {
    const list = [...members].filter((id) => ids.has(id)).sort()
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++) links.add(`${list[i]}|${list[j]}`)
  }
  return { actors, membersOf, links: [...links].map((k) => k.split("|")) }
}

/** Place each channel at the centre of its members; a one-member channel sits just outside it. */
export function placeChannels(projection, actorBoxes, membersOf) {
  const placed = new Map()
  const all = [...actorBoxes.values()]
  const cx = all.reduce((s, b) => s + b.x + b.width / 2, 0) / (all.length || 1)
  const cy = all.reduce((s, b) => s + b.y + b.height / 2, 0) / (all.length || 1)
  const size = NODE_SIZE.channel
  projection.nodes
    .filter((n) => n.kind === "channel")
    .forEach((n, i) => {
      const members = [...(membersOf.get(n.id) || [])]
        .map((id) => actorBoxes.get(id))
        .filter(Boolean)
      let x = cx + (i % 4) * (size.width + 24)
      let y = cy + 160 + Math.floor(i / 4) * 60
      if (members.length) {
        x = members.reduce((s, b) => s + b.x + b.width / 2, 0) / members.length
        y = members.reduce((s, b) => s + b.y + b.height / 2, 0) / members.length
        if (members.length === 1) {
          const dx = x - cx || 1
          const dy = y - cy || 1
          const len = Math.hypot(dx, dy)
          x += (dx / len) * 150
          y += (dy / len) * 110
        }
      }
      placed.set(n.id, {
        x: x - size.width / 2,
        y: y - size.height / 2,
        width: size.width,
        height: size.height,
      })
    })
  return placed
}

async function stressBlock(elk, actors, links) {
  if (actors.length < 2) {
    return new Map(actors.map((n) => [n.id, { x: 0, y: 0, ...sizeOf(n) }]))
  }
  const ids = new Set(actors.map((n) => n.id))
  const result = await elk.layout({
    id: "block",
    layoutOptions: {
      "elk.algorithm": "stress",
      "elk.stress.desiredEdgeLength": "380",
      "elk.spacing.nodeNode": "48",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "96",
    },
    children: actors.map((n) => ({ id: n.id, ...sizeOf(n) })),
    edges: links
      .filter(([a, b]) => ids.has(a) && ids.has(b))
      .map(([a, b], i) => ({ id: `l${i}`, sources: [a], targets: [b] })),
  })
  return new Map(
    result.children.map((c) => [
      c.id,
      { x: c.x || 0, y: c.y || 0, width: c.width, height: c.height },
    ]),
  )
}

function bounds(boxes) {
  const list = [...boxes.values()]
  const x0 = Math.min(...list.map((b) => b.x))
  const y0 = Math.min(...list.map((b) => b.y))
  const x1 = Math.max(...list.map((b) => b.x + b.width))
  const y1 = Math.max(...list.map((b) => b.y + b.height))
  return { x0, y0, x1, y1 }
}

/**
 * Shelf-pack blocks ({width, height}) in order into rows whose width
 * targets `aspect` (width / height) for the whole pack. Returns offsets.
 */
export function packBlocks(blocks, aspect, gap = BLOCK_GAP) {
  const total = blocks.reduce((s, b) => s + (b.width + gap) * (b.height + gap), 0)
  const widest = Math.max(0, ...blocks.map((b) => b.width))
  const rowLimit = Math.max(widest, Math.sqrt(total * (aspect || 1)))
  const out = []
  let x = 0
  let y = 0
  let rowHeight = 0
  for (const b of blocks) {
    if (x > 0 && x + b.width > rowLimit) {
      x = 0
      y += rowHeight + gap
      rowHeight = 0
    }
    out.push({ x, y })
    x += b.width + gap
    rowHeight = Math.max(rowHeight, b.height)
  }
  return out
}

/** Network candidate; resolves to {boxes, routes}. */
export async function networkPlacement(projection, { aspect } = {}) {
  const elk = await getElk()
  const { actors, membersOf, links } = creatureSkeleton(projection)
  const blocks = new Map()
  for (const n of actors) {
    const key = n.kind === "aggregate" ? "" : n.parent || ""
    if (!blocks.has(key)) blocks.set(key, [])
    blocks.get(key).push(n)
  }
  const laidBlocks = []
  for (const members of blocks.values()) {
    const laid = await stressBlock(elk, members, links)
    const b = bounds(laid)
    laidBlocks.push({ laid, b, width: b.x1 - b.x0, height: b.y1 - b.y0 + GROUP_PAD.top })
  }
  const offsets = packBlocks(laidBlocks, aspect)
  const actorBoxes = new Map()
  laidBlocks.forEach(({ laid, b }, i) => {
    for (const [id, box] of laid)
      actorBoxes.set(id, {
        ...box,
        x: box.x - b.x0 + offsets[i].x,
        y: box.y - b.y0 + offsets[i].y + GROUP_PAD.top,
      })
  })
  let boxes = new Map([...actorBoxes, ...placeChannels(projection, actorBoxes, membersOf)])
  if (boxes.size > 1) {
    const separated = await elk.layout({
      id: "root",
      layoutOptions: { "elk.algorithm": "sporeOverlap", "elk.spacing.nodeNode": "32" },
      children: [...boxes].map(([id, b]) => ({ id, ...b })),
    })
    boxes = new Map(
      separated.children.map((c) => [c.id, { x: c.x, y: c.y, width: c.width, height: c.height }]),
    )
  }
  const routes = new Map()
  for (const e of projection.edges) {
    const [s, t] = flowEndpoints(e)
    if (boxes.has(s) && boxes.has(t)) routes.set(e.id, orthogonalRoute(boxes.get(s), boxes.get(t)))
  }
  return { boxes, routes }
}
