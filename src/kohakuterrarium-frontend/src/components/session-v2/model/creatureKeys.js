/**
 * Creature name ⇄ chat-store tab key. When a session has a root, the chat
 * store files the privileged node's conversation, usage, jobs, busy flag
 * and model under the tab key "root" (`chat._rootSourceName` holds its real
 * name); every other creature's key is its name and channels are `ch:<name>`.
 */

/** The chat tab key for creature `name`. */
export function tabKeyFor(name, rootName) {
  return name && rootName && name === rootName ? "root" : name
}

/** The creature name behind tab key `tab` (channels and unknown keys pass through). */
export function creatureNameFor(tab, rootName) {
  return tab === "root" && rootName ? rootName : tab
}

/** What a tab is called on screen: the creature's real name, or `#channel`. */
export function tabLabel(tab, rootName) {
  if (typeof tab !== "string") return ""
  if (tab.startsWith("ch:")) return `#${tab.slice(3)}`
  return creatureNameFor(tab, rootName)
}
