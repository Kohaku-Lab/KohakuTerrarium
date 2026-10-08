/**
 * Pure derivations for the v2 session shell: the session tabs, the
 * conversations the bar's switcher offers, and the dock items that have
 * content. Inputs are counts and short lists the stores already hold.
 */

import { tabKeyFor } from "./creatureKeys"
import { visibleChannels } from "./sessionChannels"

/** The fixed session tabs, in display order. */
export const SESSION_TABS = [
  { id: "chat", icon: "i-carbon-chat" },
  { id: "status", icon: "i-carbon-dashboard" },
  { id: "workspace", icon: "i-carbon-code" },
  { id: "debug", icon: "i-carbon-debug" },
  { id: "settings", icon: "i-carbon-settings" },
]

/**
 * Conversations of a session: privileged nodes first, then the other
 * creatures, then designed channels. Each entry is {key, kind: "creature" |
 * "channel", name, status, privileged}; `key` is the chat store's tab key
 * (the privileged node under "root" when `rootName` names it), `name` the
 * real name. Each creature appears once.
 */
export function conversationOptions(instance, rootName = null) {
  const creatures = [...(instance?.creatures || [])].sort(
    (a, b) => Number(isPrivileged(b)) - Number(isPrivileged(a)),
  )
  const out = creatures.map((c) => ({
    key: tabKeyFor(c.name, rootName),
    kind: "creature",
    name: c.name,
    status: creatureStatus(c),
    privileged: isPrivileged(c),
  }))
  for (const ch of visibleChannels(instance))
    out.push({
      key: `ch:${ch.name}`,
      kind: "channel",
      name: ch.name,
      status: null,
      privileged: false,
    })
  return out
}

/**
 * Members of a channel: every creature that sends to or listens on it,
 * privileged first then by name — {name, key, status, privileged, sends,
 * listens}; `key` is the creature's chat tab key.
 */
export function channelMembers(instance, channel, rootName = null) {
  return (instance?.creatures || [])
    .map((c) => ({
      name: c.name,
      key: tabKeyFor(c.name, rootName),
      status: creatureStatus(c),
      privileged: isPrivileged(c),
      sends: (c.send_channels || []).includes(channel),
      listens: (c.listen_channels || []).includes(channel),
    }))
    .filter((m) => m.sends || m.listens)
    .sort((a, b) => Number(b.privileged) - Number(a.privileged) || a.name.localeCompare(b.name))
}

export function isPrivileged(c) {
  return !!(c?.is_privileged || c?.is_root)
}

/** "running" or "stopped" from a mapped instance creature. */
export function creatureStatus(c) {
  return c?.running === false || c?.status === "stopped" ? "stopped" : "running"
}

/**
 * Dock items with content right now, in display order. `facts`:
 * {drives, jobs, artifacts} — counts, shown past zero — plus optional
 * `usage` {tokens, label, title}. Agents and channels live in the Chat
 * tab's rail, so they are not dock items. Counted items carry `count`;
 * usage carries only its `label` and `title`, so it never adds to the
 * dock's total.
 */
export function dockItems(facts) {
  const usage = facts.usage || { tokens: 0, label: "" }
  return [
    {
      id: "usage",
      icon: "i-carbon-meter",
      label: usage.label,
      title: usage.title,
      show: usage.tokens > 0,
    },
    { id: "drives", icon: "i-carbon-task", count: facts.drives, show: facts.drives > 0 },
    { id: "jobs", icon: "i-carbon-in-progress", count: facts.jobs, show: facts.jobs > 0 },
    { id: "canvas", icon: "i-carbon-image", count: facts.artifacts, show: facts.artifacts > 0 },
  ]
    .filter((i) => i.show)
    .map(({ show: _show, ...rest }) => rest)
}

/**
 * The dock's "More" menu: always reachable whatever the session holds.
 * `target` says whether the entry opens a floating widget or the side view.
 */
export const MORE_ITEMS = [
  { id: "usage", icon: "i-carbon-meter", target: "widget" },
  { id: "agents", icon: "i-carbon-bot", target: "widget" },
  { id: "channels", icon: "i-carbon-flow-stream", target: "widget" },
  { id: "scratchpad", icon: "i-carbon-notebook", target: "widget" },
  { id: "plugins", icon: "i-carbon-plug", target: "widget" },
  { id: "search", icon: "i-carbon-search", target: "widget" },
  { id: "terminal", icon: "i-carbon-terminal", target: "side" },
  { id: "graph", icon: "i-carbon-network-3", target: "side" },
]
