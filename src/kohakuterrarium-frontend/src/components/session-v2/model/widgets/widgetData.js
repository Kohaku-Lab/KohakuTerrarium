/**
 * Pure row builders for the v2 dock widgets and side views. Each reads
 * short lists the stores already hold (creatures, channels, running jobs,
 * canvas artifacts); none touches chat messages.
 */

import { creatureNameFor } from "../creatureKeys"
import { visibleChannels } from "../sessionChannels"

/**
 * The creature a per-creature widget acts on, by real name: the open
 * creature conversation (the "root" tab is `rootName`), or a solo
 * session's only creature.
 */
export function focusedCreature(instance, activeTab, rootName = null) {
  const creatures = instance?.creatures || []
  if (!creatures.length) return null
  if (activeTab && !activeTab.startsWith("ch:")) return creatureNameFor(activeTab, rootName)
  return creatures.length === 1 ? creatures[0].name : null
}

/** The model label a creature reports. */
export function creatureModel(c) {
  return c?.llm_name || c?.model || ""
}

/** Designed channels with member and unread counts, in session order. */
export function channelRows(instance, unreadCounts = {}) {
  const creatures = instance?.creatures || []
  return visibleChannels(instance).map((ch) => {
    const listeners = creatures
      .filter((c) => (c.listen_channels || []).includes(ch.name))
      .map((c) => c.name)
    const senders = creatures
      .filter((c) => (c.send_channels || []).includes(ch.name))
      .map((c) => c.name)
    return {
      name: ch.name,
      key: `ch:${ch.name}`,
      listeners,
      senders,
      members: new Set([...listeners, ...senders]).size,
      unread: unreadCounts[`ch:${ch.name}`] || 0,
    }
  })
}

/** Elapsed seconds since `startedAt` as a short label ("" when unknown). */
export function elapsedLabel(startedAt, now = Date.now()) {
  if (!startedAt) return ""
  const secs = Math.max(0, Math.floor((now - startedAt) / 1000))
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ${secs % 60}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

const TYPE_ICONS = {
  code: "i-carbon-code",
  markdown: "i-carbon-document",
  html: "i-carbon-html",
  svg: "i-carbon-image",
  image: "i-carbon-image",
  diagram: "i-carbon-flow-connection",
  table: "i-carbon-data-table",
}

export function artifactIcon(type) {
  return TYPE_ICONS[type] || "i-carbon-document"
}

/** Download file name for a canvas artifact, extension from its type. */
export function artifactFileName(art) {
  const ext =
    {
      code: art?.lang || "txt",
      markdown: "md",
      html: "html",
      svg: "svg",
      image: art?.lang || "png",
      diagram: "mmd",
      table: "csv",
    }[art?.type] || "txt"
  return `${(art?.name || "artifact").replace(/[^a-zA-Z0-9_.-]/g, "_")}.${ext}`
}

/** Which viewer shows an artifact: image, markdown, html, or code (everything else). */
export function artifactViewer(art) {
  const type = art?.type
  if (type === "image" || type === "markdown" || type === "html") return type
  return "code"
}

const MODULE_TYPE_ORDER = ["plugin", "native_tool", "tool", "trigger"]

/** Modules grouped by type (plugins first), each group by priority then name: [{type, items}]. */
export function moduleGroups(modules = []) {
  const groups = new Map()
  for (const m of modules) {
    if (!groups.has(m.type)) groups.set(m.type, [])
    groups.get(m.type).push(m)
  }
  const rank = (type) => {
    const i = MODULE_TYPE_ORDER.indexOf(type)
    return i < 0 ? MODULE_TYPE_ORDER.length : i
  }
  return [...groups.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([type, items]) => ({
      type,
      items: [...items].sort(
        (a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9) || a.name.localeCompare(b.name),
      ),
    }))
}

/** Scratchpad entries as sorted [key, value] rows; values are strings. */
export function scratchpadRows(data) {
  return Object.entries(data || {})
    .map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)])
    .sort((a, b) => a[0].localeCompare(b[0]))
}
