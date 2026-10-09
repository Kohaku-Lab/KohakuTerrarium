/**
 * Pure rows of the phone session layout: the conversations sheet (agents
 * then channels, each with what a phone row shows) and the tools the ⋯
 * menu offers. Inputs are the instance snapshot and the chat store's
 * per-tab maps.
 */

import { creatureNameFor } from "../creatureKeys"
import { MORE_ITEMS, conversationOptions } from "../sessionModel"

/** The model a creature runs, live from the chat store first. */
export function modelOfCreature(instance, chat, name) {
  if (!name) return ""
  const key = (
    conversationOptions(instance, chat?._rootSourceName).find(
      (o) => o.kind === "creature" && o.name === name,
    ) || {}
  ).key
  const live = (key && chat?.modelByTab?.[key]) || chat?.modelByTab?.[name]
  const c = (instance?.creatures || []).find((x) => x.name === name)
  return live?.llmName || live?.model || c?.llm_name || c?.model || ""
}

/**
 * Conversation rows: {key, kind, name, status, privileged, busy, unread,
 * active, detail}. `detail` is an agent's model or a channel's description.
 */
export function conversationRows(instance, chat) {
  const rootName = chat?._rootSourceName || null
  const channels = new Map((instance?.channels || []).map((c) => [c.name, c]))
  return conversationOptions(instance, rootName).map((o) => ({
    ...o,
    busy: !!chat?.processingByTab?.[o.key],
    unread: o.key === chat?.activeTab ? 0 : chat?.unreadCounts?.[o.key] || 0,
    active: o.key === chat?.activeTab,
    detail:
      o.kind === "creature"
        ? modelOfCreature(instance, chat, o.name)
        : channels.get(o.name)?.description || "",
  }))
}

/** The creature whose conversation `tab` is, or "" for a channel or nothing. */
export function creatureOfTab(instance, tab, rootName) {
  if (!tab || tab.startsWith("ch:")) return ""
  const name = creatureNameFor(tab, rootName)
  return (instance?.creatures || []).some((c) => c.name === name) ? name : ""
}

/**
 * The ⋯ menu's tools: counted live items (drives, jobs, artifacts) first,
 * then every always-available tool. `graph` opens the Graph tab, so it is
 * not listed. Each is {id, icon, target: "widget" | "side", count}.
 */
export function phoneTools(facts = {}) {
  const live = [
    { id: "drives", icon: "i-carbon-task", target: "widget", count: facts.drives || 0 },
    { id: "jobs", icon: "i-carbon-in-progress", target: "widget", count: facts.jobs || 0 },
    { id: "canvas", icon: "i-carbon-image", target: "widget", count: facts.artifacts || 0 },
  ].filter((t) => t.count > 0)
  const always = MORE_ITEMS.filter((t) => t.id !== "graph").map((t) => ({ ...t, count: 0 }))
  return [...live, ...always]
}
