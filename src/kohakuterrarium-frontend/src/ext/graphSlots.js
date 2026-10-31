/**
 * Graph badges and graph panels: package UI attached to graph nodes.
 *
 * A badge is a small component drawn on a node of the canvas; a panel is a
 * section in the graph dock's details for the selected node. Both name the
 * node kinds they apply to (``creature``, ``channel``, ``group``) and read a
 * read-only description of the node.
 */

import { markRaw, reactive } from "vue"

export const GRAPH_NODE_KINDS = ["creature", "channel", "group"]

export const graphBadges = reactive(new Map())
export const graphPanels = reactive(new Map())

function entry(fields) {
  const { component, options, order, label, id } = fields
  return {
    ...fields,
    label: label || id,
    order: Number.isFinite(order) ? order : 100,
    component: markRaw(component),
    nodes: markRaw([...(options?.nodes || [])]),
  }
}

export function registerGraphBadge(fields) {
  graphBadges.set(fields.key, entry(fields))
}

export function registerGraphPanel(fields) {
  graphPanels.set(fields.key, entry(fields))
}

export function clearGraphSlots() {
  graphBadges.clear()
  graphPanels.clear()
}

function forKind(map, kind) {
  return [...map.values()].filter((e) => e.nodes.includes(kind)).sort((a, b) => a.order - b.order)
}

export const badgesFor = (kind) => forKind(graphBadges, kind)
export const panelsFor = (kind) => forKind(graphPanels, kind)

/** The read-only description of a graph node handed to badges, panels and actions. */
export function graphNodeInfo(kind, item) {
  const source = item || {}
  return {
    kind,
    id: source.id ?? source.key ?? source.name ?? "",
    name: source.name ?? source.label ?? "",
    sessionId: source.sessionId ?? null,
  }
}
