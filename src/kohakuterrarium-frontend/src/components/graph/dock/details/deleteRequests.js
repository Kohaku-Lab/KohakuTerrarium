/**
 * Build the confirmation shown before a destructive graph action, with the
 * consequences computed from the current topology. Edges need no confirm.
 */

import { predictChannelRemoval, predictRemoval } from "@/utils/graph/data/model"

function splitLines(t, components) {
  if (components.length <= 1) return []
  return [
    t("graph.confirm.splits", { n: components.length }),
    ...components.map(
      (names) =>
        `· ${names.slice(0, 6).join(", ")}${names.length > 6 ? ` +${names.length - 6}` : ""}`,
    ),
  ]
}

export function creatureDeleteRequest(view, actions, t, creature) {
  const lines = [t("graph.confirm.removeCreatureBody", { name: creature.name })]
  if (creature.status === "busy") lines.push(t("graph.confirm.interruptsTurn"))
  if (creature.privileged) lines.push(t("graph.confirm.privileged"))
  lines.push(...splitLines(t, predictRemoval(view.model, creature.id)))
  return {
    title: t("graph.confirm.removeCreature", { name: creature.name }),
    lines,
    confirmLabel: t("graph.action.remove"),
    run: () => actions.removeCreature(creature),
  }
}

export function channelDeleteRequest(view, actions, t, channel) {
  const members = new Set([...channel.senders, ...channel.listeners]).size
  const lines = [t("graph.confirm.removeChannelBody", { name: channel.name, n: members })]
  lines.push(...splitLines(t, predictChannelRemoval(view.model, channel.id)))
  return {
    title: t("graph.confirm.removeChannel", { name: channel.name }),
    lines,
    confirmLabel: t("graph.action.remove"),
    run: () => actions.removeChannel(channel),
  }
}

export function sessionStopRequest(view, actions, t, session) {
  return {
    title: t("graph.confirm.stopSession", { name: session.name }),
    lines: [t("graph.confirm.stopSessionBody", { n: session.creatureIds.length })],
    confirmLabel: t("graph.action.stopSession"),
    run: () => actions.stopSession(session.id),
  }
}

/** Request for the current selection, or null when nothing needs confirming. */
export function deleteRequestForSelection(view, actions, t) {
  const sel = view.selected
  if (!sel) return null
  if (sel.kind === "creature") return creatureDeleteRequest(view, actions, t, sel.item)
  if (sel.kind === "channel") return channelDeleteRequest(view, actions, t, sel.item)
  if (sel.kind === "edge") {
    if (sel.item.kind === "wire" || sel.item.kind === "channel") actions.removeEdge(sel.item)
    return null
  }
  if (sel.kind === "group" && sel.item.kind === "session") {
    const session = view.model.sessions.find((s) => s.id === sel.item.key)
    return session ? sessionStopRequest(view, actions, t, session) : null
  }
  return null
}
