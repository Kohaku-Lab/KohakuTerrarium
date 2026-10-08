/**
 * Visual roles for the graph view, drawn only from the gem palette:
 * channel = aquamarine, direct creature → creature reach = sage,
 * output wire = sapphire, sub-agent = taaffeite, focus/selection and the
 * control plane (privileged nodes, their channel links) = iolite, status per
 * StatusDot semantics, lineage = warm.
 */

import { GEM } from "@/utils/colors"

export const EDGE_COLOR = Object.freeze({
  channel: GEM.aquamarine.main,
  via: GEM.aquamarine.main,
  direct: GEM.sage.main,
  wire: GEM.sapphire.main,
  lineage: "#A09A92",
  authority: "#8A8480",
  control: GEM.iolite.main,
  focus: GEM.iolite.main,
})

export const STATUS_STYLE = Object.freeze({
  busy: { glyph: "●", text: "text-aquamarine", ring: "ring-aquamarine/50", dot: "bg-aquamarine" },
  idle: { glyph: "○", text: "text-amber", ring: "ring-amber/30", dot: "bg-amber" },
  paused: { glyph: "‖", text: "text-amber", ring: "ring-amber/40", dot: "bg-amber" },
  stopped: { glyph: "■", text: "text-warm-400", ring: "ring-warm-400/30", dot: "bg-warm-400" },
  error: { glyph: "!", text: "text-coral", ring: "ring-coral/50", dot: "bg-coral" },
})

export function statusStyle(status) {
  return STATUS_STYLE[status] || STATUS_STYLE.idle
}

const GROUP_ICON = Object.freeze({
  host: "i-carbon-bare-metal-server",
  control: "i-carbon-security",
  session: "i-carbon-network-4",
})

const GROUP_TITLE_KEY = Object.freeze({
  control: "graph.group.privileged",
})

export function groupIcon(kind) {
  return GROUP_ICON[kind] || GROUP_ICON.session
}

/** Display title of a non-host group; a privileged-node group carries its session name only when several are shown. */
export function groupTitle(group, t) {
  const key = GROUP_TITLE_KEY[group.kind]
  if (!key) return group.label
  return group.label ? `${t(key)} · ${group.label}` : t(key)
}

/** Zoom → level of detail used by node components. */
export function lodForZoom(zoom) {
  if (zoom < 0.45) return "far"
  if (zoom < 0.8) return "mid"
  return "near"
}
