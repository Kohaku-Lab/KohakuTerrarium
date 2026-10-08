/**
 * Vue Flow element builders shared by the node-link views: group backdrops
 * and edges carrying their route, styling flags and menu target.
 */

import { MarkerType } from "@vue-flow/core"

import { EDGE_COLOR } from "@/components/graph/graphTheme"
import { groupBackdrops } from "@/utils/graph/layout/place/elk"

export function marker(color, type = MarkerType.ArrowClosed) {
  return { type, color, width: 16, height: 16 }
}

/** Backdrop nodes for expanded groups, sized around their members' boxes. */
export function backdropElements(groups, boxes, view) {
  return groupBackdrops(groups, boxes).map((b) => ({
    id: b.group.id,
    type: "group",
    position: { x: b.x, y: b.y },
    // The body lets clicks through to the canvas; the header is the grab handle that moves the members.
    style: { width: `${b.width}px`, height: `${b.height}px`, pointerEvents: "none" },
    zIndex: -1,
    draggable: true,
    dragHandle: ".kt-graph-backdrop-head",
    connectable: false,
    selected: view.selection?.id === b.group.id,
    data: {
      target: { kind: "group", item: b.group },
      group: b.group,
      onToggleCollapse: view.toggleCollapse,
    },
  }))
}

/**
 * One edge element. `e` is a projection edge (or a view-specific edge with
 * the same fields); `source` / `target` are the drawn direction.
 */
export function edgeElement(
  e,
  {
    source,
    target,
    points = null,
    labelPos = null,
    bend = 0,
    selected = false,
    dimmed = false,
    lit = false,
    pulseAt = 0,
    label = "",
    menu = true,
  },
) {
  const color = selected
    ? EDGE_COLOR.focus
    : e.control && e.kind === "channel"
      ? EDGE_COLOR.control
      : EDGE_COLOR[e.kind] || EDGE_COLOR.channel
  const arrow =
    e.kind === "lineage" || e.kind === "authority"
      ? undefined
      : marker(color, e.kind === "direct" ? MarkerType.Arrow : MarkerType.ArrowClosed)
  // A layout-reversed edge is routed head-first, so its arrow sits at the start.
  const both = e.kind === "channel" && e.mode === "both"
  return {
    id: e.id,
    type: "graph",
    source,
    target,
    selected,
    markerEnd: e.layoutReverse && !both ? undefined : arrow,
    markerStart: both || e.layoutReverse ? arrow : undefined,
    data: {
      target: menu ? { kind: "edge", item: e } : null,
      kind: e.kind,
      mode: e.mode,
      count: e.count || 1,
      labels: e.labels,
      channelName: e.channelName,
      withContent: e.withContent,
      ping: !!e.ping,
      prompt: e.prompt,
      back: !!e.back,
      control: !!e.control,
      implicit: !!e.implicit,
      bend,
      points,
      labelPos,
      dimmed,
      lit,
      pulseAt,
      label,
    },
  }
}
