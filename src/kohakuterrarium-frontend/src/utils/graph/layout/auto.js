/**
 * Layout entry point. Each view runs one or more candidate placements and
 * keeps the one with the lowest place-and-route cost (crossings, edges
 * through nodes, overlap, wire length, and area fitted to the viewport's
 * aspect ratio).
 */

import { labelDrawing } from "@/utils/graph/layout/labels"
import { measureLayout } from "@/utils/graph/layout/metrics"
import {
  DOCK_SIDES,
  dockBlock,
  slotDocks,
  stripDock,
  withoutDock,
} from "@/utils/graph/layout/place/dock"
import { flowEndpoints, layeredPlacement } from "@/utils/graph/layout/place/elk"
import { networkPlacement } from "@/utils/graph/layout/place/network"
import { stackedTree, treePlacement } from "@/utils/graph/layout/place/tree"

export const DEFAULT_ASPECT = 16 / 10
const DOCK_ON = 2

const layered =
  (direction, variant = {}) =>
  (p, ctx) =>
    layeredPlacement(p, direction, { ...variant, aspect: ctx.aspect })

const FLOW = { modelOrder: true }

/** Candidate placements per view mode; each runs as (projection, {aspect}). */
export const CANDIDATES = Object.freeze({
  network: [
    { name: "layered-right", run: layered("RIGHT") },
    { name: "layered-right-bk", run: layered("RIGHT", { placement: "BRANDES_KOEPF" }) },
    { name: "layered-down", run: layered("DOWN") },
    { name: "layered-down-bk", run: layered("DOWN", { placement: "BRANDES_KOEPF" }) },
    { name: "network", run: (p, ctx) => networkPlacement(p, ctx) },
  ],
  flow: [
    { name: "flow-right", run: layered("RIGHT", FLOW) },
    { name: "flow-right-bk", run: layered("RIGHT", { ...FLOW, placement: "BRANDES_KOEPF" }) },
    { name: "flow-down", run: layered("DOWN", FLOW) },
    { name: "flow-down-bk", run: layered("DOWN", { ...FLOW, placement: "BRANDES_KOEPF" }) },
  ],
  tiers: [
    { name: "tree-stack-2", run: async (p) => stackedTree(p, { cols: 2 }) },
    { name: "tree-stack-1", run: async (p) => stackedTree(p, { cols: 1 }) },
    { name: "tree", run: (p, ctx) => treePlacement(p, { ...ctx, algorithm: "mrtree" }) },
    { name: "tree-layered", run: (p, ctx) => treePlacement(p, { ...ctx, algorithm: "layered" }) },
  ],
})

function routeList(projection, routes) {
  const out = []
  for (const e of projection.edges) {
    const points = routes.get(e.id)
    if (!points || e.layoutOnly) continue
    const [source, target] = flowEndpoints(e)
    out.push({ id: e.id, source, target, points })
  }
  return out
}

/**
 * Lay out `projection` for `mode` against a viewport of `aspect`
 * (width / height). When `projection.dock` names a group ({groupId,
 * memberIds}), the best plain candidates are also re-docked: the group
 * moved on that very layout, and laid out without it, each docked on every
 * side and in the best free slots; all variants compete on the same cost.
 * `side` edges are routed by the view after placement and take no part here.
 * Resolves to
 * {boxes, routes, labels, chosen, metrics, tried: [{name, metrics}]}.
 */
export async function layoutGraph(input, mode = "network", { aspect = DEFAULT_ASPECT } = {}) {
  const projection = input.edges.some((e) => e.side)
    ? { ...input, edges: input.edges.filter((e) => !e.side) }
    : input
  const candidates = CANDIDATES[mode] || CANDIDATES.network
  const ctx = { aspect: aspect > 0 ? aspect : DEFAULT_ASPECT }
  const tried = []
  let best = null
  const consider = (name, placed) => {
    // Every candidate is labelled the same way, so how cleanly it labels is part of its cost.
    const { labels, hits } = labelDrawing(projection, placed)
    const metrics = measureLayout(placed.boxes, routeList(projection, placed.routes), {
      ...ctx,
      labelHits: hits,
    })
    tried.push({ name, metrics })
    if (!best || metrics.cost < best.metrics.cost)
      best = { ...placed, labels, chosen: name, metrics }
  }
  const attempt = async (name, run) => {
    try {
      return await run()
    } catch (err) {
      tried.push({ name, error: err?.message || String(err) })
      return null
    }
  }
  // A projection with a `dock` group is also laid out without it, the group then docked on each side.
  const docked = projection.dock?.memberIds?.length ? withoutDock(projection) : null
  const dockOnto = (name, base) => {
    for (const side of DOCK_SIDES) consider(`${name}-${side}`, dockBlock(base, projection, side))
    for (const slot of slotDocks(base, projection, ctx.aspect))
      consider(`${name}-${slot.name}`, slot.placed)
  }
  const plain = []
  for (const candidate of candidates) {
    const placed = await attempt(candidate.name, () => candidate.run(projection, ctx))
    if (!placed) continue
    consider(candidate.name, placed)
    plain.push({ candidate, placed, cost: tried[tried.length - 1].metrics.cost })
  }
  // Docking is tried on the best plain layouts only; the rest rarely win and cost a full layout each.
  if (docked)
    for (const { candidate, placed } of plain.sort((a, b) => a.cost - b.cost).slice(0, DOCK_ON)) {
      // Re-placing the group on this very layout can only move the group, never disturb the rest.
      dockOnto(`${candidate.name}~dock`, stripDock(placed, projection))
      const base = await attempt(`${candidate.name}+dock`, () => candidate.run(docked, ctx))
      if (base) dockOnto(`${candidate.name}+dock`, base)
    }
  if (!best)
    throw new Error(
      tried.map((t) => `${t.name}: ${t.error}`).join("; ") || "no layout candidate succeeded",
    )
  return { ...best, tried }
}
