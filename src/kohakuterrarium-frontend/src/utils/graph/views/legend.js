/**
 * Legend entries for the graph view on screen: one key per visual element
 * the view actually draws, so the legend never explains what is absent and
 * never misses what is there. Pure.
 */

import { DEFAULT_PRIVILEGED_LINKS, buildFlowInput } from "@/utils/graph/views/flow"
import { buildTierInput } from "@/utils/graph/views/tiers"

/** Every legend key, in display order. */
export const LEGEND_KEYS = [
  "membership",
  "handoff",
  "control",
  "direct",
  "bundle",
  "wire",
  "ping",
  "back",
  "lineage",
  "spawn",
  "group",
  "hub",
  "room",
  "privilegedChip",
  "wireChip",
  "busListen",
  "busSend",
  "sendPort",
  "wirePort",
]

function wireKeys(edges, out) {
  for (const e of edges) {
    if (e.kind !== "wire") continue
    out.add(e.withContent === false ? "ping" : "wire")
  }
}

function networkKeys(projection, out) {
  for (const e of projection.edges) {
    if (e.kind === "channel") out.add(e.control ? "control" : "membership")
    if (e.kind === "lineage") out.add("lineage")
    if (e.kind === "direct") out.add("direct")
  }
  wireKeys(projection.edges, out)
  if (projection.groups.some((g) => g.kind === "control" && !g.collapsed)) out.add("group")
  if (projection.nodes.some((n) => n.kind === "creature")) out.add("sendPort").add("wirePort")
}

function flowKeys(flow, out) {
  for (const e of flow.edges) {
    if (e.layoutOnly) continue
    if (e.kind === "via") out.add("handoff")
    if (e.kind === "channel" && e.control) out.add(e.ping ? "ping" : "control")
    if (e.back) out.add("back")
  }
  wireKeys(flow.edges, out)
  if (flow.bundles.length) out.add("bundle")
  if (flow.groups.length) out.add("group")
  if (flow.nodes.some((n) => n.kind === "hub")) out.add("hub")
  const stages = flow.nodes.filter((n) => n.kind === "stage")
  if (stages.some((n) => n.rooms?.length)) out.add("room")
  if (stages.some((n) => n.inlets?.length || n.outlets?.length || n.wireChips?.length))
    out.add("privilegedChip")
  for (const s of stages) for (const w of s.wireChips || []) out.add(w.ping ? "ping" : "wire")
  if (stages.length) out.add("sendPort").add("wirePort")
}

/**
 * Legend keys (LEGEND_KEYS order) for `mode` over `projection`;
 * `privilegedLinks` is the Flow knob (see flow.js).
 */
export function legendKeys(mode, projection, { privilegedLinks = DEFAULT_PRIVILEGED_LINKS } = {}) {
  const out = new Set()
  switch (mode) {
    case "flow":
      flowKeys(buildFlowInput(projection, { privilegedLinks }), out)
      break
    case "tiers": {
      const tree = buildTierInput(projection)
      if (tree.edges.length) out.add("spawn")
      // The spawn tree shows wires as chips on the cards, not as lines.
      if (tree.nodes.some((n) => n.wiresOut?.length)) out.add("wireChip")
      break
    }
    case "bus":
      if (projection.channels.length) out.add("busListen").add("busSend")
      wireKeys(projection.edges, out)
      break
    default:
      networkKeys(projection, out)
  }
  return LEGEND_KEYS.filter((k) => out.has(k))
}
