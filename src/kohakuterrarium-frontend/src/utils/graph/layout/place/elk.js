/**
 * ELK runner and the layered placements. Every placement returns absolute
 * node boxes plus orthogonal routes (`points`, source → target) per edge;
 * groups are drawn later as backdrops around their members.
 */

import ElkApi from "elkjs/lib/elk-api.js"
import elkWorkerUrl from "elkjs/lib/elk-worker.min.js?url"

export const NODE_SIZE = Object.freeze({
  creature: { width: 208, height: 76 },
  aggregate: { width: 208, height: 76 },
  channel: { width: 152, height: 34 },
  stage: { width: 196, height: 62 },
  junction: { width: 132, height: 26 },
  hub: { width: 150, height: 24 },
  privileged: { width: 188, height: 48 },
  team: { width: 220, height: 56 },
  tier: { width: 312, height: 128 },
  user: { width: 140, height: 40 },
  session: { width: 220, height: 44 },
})

export const GROUP_PAD = Object.freeze({ top: 44, side: 22, bottom: 22 })

let _elk = null

export async function getElk() {
  if (_elk) return _elk
  if (typeof Worker !== "undefined") {
    _elk = new ElkApi({ workerUrl: elkWorkerUrl })
  } else {
    const { default: ElkBundled } = await import("elkjs/lib/elk.bundled.js")
    _elk = new ElkBundled()
  }
  return _elk
}

/**
 * Layout direction of an edge: along message flow (listen edges travel
 * channel → creature), flipped when `layoutReverse` is set.
 */
export function flowEndpoints(edge) {
  const along =
    edge.kind === "channel" && edge.mode === "listen"
      ? [edge.target, edge.source]
      : [edge.source, edge.target]
  return edge.layoutReverse ? [along[1], along[0]] : along
}

/** Rendered size of an edge label chip (10px mono text plus padding). */
export function labelSize(text) {
  return { width: Math.ceil(text.length * 6.2 + 14), height: 18 }
}

export function sizeOf(node) {
  return node.size || NODE_SIZE[node.kind] || NODE_SIZE.creature
}

/**
 * Layered options for one candidate. `variant`: placement, cycles,
 * modelOrder (input order = flow rank), aspect (viewport width / height).
 */
export function layeredOptions(direction, variant = {}) {
  return {
    "elk.algorithm": "layered",
    "elk.direction": direction,
    "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.edgeRouting": "ORTHOGONAL",
    "elk.json.edgeCoords": "ROOT",
    "elk.spacing.nodeNode": "36",
    "elk.spacing.edgeNode": "18",
    "elk.spacing.edgeEdge": "12",
    "elk.spacing.componentComponent": "72",
    "elk.layered.spacing.nodeNodeBetweenLayers": "64",
    "elk.layered.spacing.edgeNodeBetweenLayers": "18",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
    "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    "elk.layered.crossingMinimization.thoroughness": "40",
    "elk.layered.nodePlacement.strategy": variant.placement || "NETWORK_SIMPLEX",
    "elk.layered.nodePlacement.favorStraightEdges": "true",
    "elk.layered.cycleBreaking.strategy": variant.cycles || "DEPTH_FIRST",
    "elk.layered.compaction.postCompaction.strategy": "EDGE_LENGTH",
    "elk.layered.mergeEdges": "false",
    ...(variant.modelOrder
      ? {
          // Input order is the flow rank, so cycles break against it instead of arbitrarily.
          "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
          "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        }
      : {}),
    // Disconnected components are packed towards the viewport's shape.
    ...(variant.aspect ? { "elk.aspectRatio": String(variant.aspect) } : {}),
  }
}

/** Build the ELK input graph for a layered candidate (exported for tests). */
export function buildLayeredGraph(projection, direction, variant = {}) {
  const parents = new Set(projection.nodes.map((n) => n.parent).filter(Boolean))
  const containerIds = projection.groups
    .filter((g) => !g.collapsed && parents.has(g.id))
    .map((g) => g.id)
  const known = new Set([...projection.nodes.map((n) => n.id), ...containerIds])
  const laid = projection.edges.filter(
    (e) =>
      known.has(e.source) && known.has(e.target) && (e.kind !== "lineage" || variant.withLineage),
  )
  // ELK rejects a FIRST node with an incoming edge and a LAST node with an outgoing one.
  const hasIn = new Set(laid.map((e) => flowEndpoints(e)[1]))
  const hasOut = new Set(laid.map((e) => flowEndpoints(e)[0]))
  const leaf = (node) => {
    const size = sizeOf(node)
    const out = { id: node.id, width: size.width, height: size.height }
    if (node.pin === "first" && !hasIn.has(node.id))
      out.layoutOptions = { "elk.layered.layering.layerConstraint": "FIRST" }
    else if (node.pin === "last" && !hasOut.has(node.id))
      out.layoutOptions = { "elk.layered.layering.layerConstraint": "LAST" }
    return out
  }
  const ordered = variant.modelOrder
    ? [...projection.nodes].sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
    : projection.nodes
  const padding = `[top=${GROUP_PAD.top},left=${GROUP_PAD.side},bottom=${GROUP_PAD.bottom},right=${GROUP_PAD.side}]`
  const containers = new Map()
  for (const g of projection.groups) {
    if (!g.collapsed)
      containers.set(g.id, { id: g.id, layoutOptions: { "elk.padding": padding }, children: [] })
  }
  // A container enters the input where its first member does, so model order
  // (and MODEL_ORDER cycle breaking) sees it at its members' rank.
  const roots = []
  for (const node of ordered) {
    const parent = node.parent && containers.get(node.parent)
    if (!parent) {
      roots.push(leaf(node))
      continue
    }
    if (!parent.children.length) roots.push(parent)
    parent.children.push(leaf(node))
  }

  const edges = []
  for (const e of laid) {
    const [source, target] = flowEndpoints(e)
    const edge = { id: e.id, sources: [source], targets: [target] }
    // Labelled edges reserve room for their label so it never overlaps a node or another label.
    if (e.layoutLabel)
      edge.labels = [{ id: `${e.id}:label`, text: e.layoutLabel, ...labelSize(e.layoutLabel) }]
    edges.push(edge)
  }
  const options = layeredOptions(direction, variant)
  if (edges.some((e) => e.labels)) {
    // Inline: the label sits on the line, so it can only be read as belonging to that line.
    options["elk.edgeLabels.placement"] = "CENTER"
    options["elk.edgeLabels.inline"] = "true"
    options["elk.spacing.edgeLabel"] = "2"
    // ELK's post-compaction rejects inline label dummies ("Invalid hitboxes for scanline constraint").
    options["elk.layered.compaction.postCompaction.strategy"] = "NONE"
  }
  return { id: "root", layoutOptions: options, children: roots, edges }
}

function collectBoxes(node, out, ox, oy, containerIds) {
  for (const child of node.children || []) {
    const x = ox + (child.x || 0)
    const y = oy + (child.y || 0)
    if (!containerIds.has(child.id))
      out.set(child.id, { x, y, width: child.width, height: child.height })
    collectBoxes(child, out, x, y, containerIds)
  }
}

function collectRoutes(node, routes, labels) {
  for (const e of node.edges || []) {
    const section = e.sections?.[0]
    if (!section) continue
    routes.set(
      e.id,
      [section.startPoint, ...(section.bendPoints || []), section.endPoint].map((p) => ({
        x: p.x,
        y: p.y,
      })),
    )
    const label = e.labels?.[0]
    if (label && label.x != null)
      labels.set(e.id, { x: label.x + label.width / 2, y: label.y + label.height / 2 })
  }
  for (const child of node.children || []) collectRoutes(child, routes, labels)
}

/** Run one layered candidate; resolves to {boxes, routes}. */
export async function layeredPlacement(projection, direction, variant = {}) {
  const elk = await getElk()
  const graph = buildLayeredGraph(projection, direction, variant)
  const containerIds = new Set(graph.children.filter((c) => c.children).map((c) => c.id))
  const result = await elk.layout(graph)
  const boxes = new Map()
  collectBoxes(result, boxes, 0, 0, containerIds)
  const routes = new Map()
  const labels = new Map()
  collectRoutes(result, routes, labels)
  return { boxes, routes, labels }
}

/** Backdrop rectangle per expanded group from its members' current boxes. */
export function groupBackdrops(groups, boxes) {
  const out = []
  for (const g of groups) {
    if (g.collapsed) continue
    const members = [...g.creatureIds, ...g.channelIds, ...(g.extraIds || [])]
      .map((id) => boxes.get(id))
      .filter(Boolean)
    if (!members.length) continue
    const x0 = Math.min(...members.map((b) => b.x)) - GROUP_PAD.side
    const y0 = Math.min(...members.map((b) => b.y)) - GROUP_PAD.top
    const x1 = Math.max(...members.map((b) => b.x + b.width)) + GROUP_PAD.side
    const y1 = Math.max(...members.map((b) => b.y + b.height)) + GROUP_PAD.bottom
    out.push({ group: g, x: x0, y: y0, width: x1 - x0, height: y1 - y0 })
  }
  return out
}
