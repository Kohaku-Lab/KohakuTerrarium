/**
 * The widget object a renderer receives, built from a chat message.
 *
 * A ``role: "widget"`` message is a generic widget; a ``role: "ui_event"``
 * message is one of the legacy kinds and maps to ``kt.<kind>`` with its old
 * payload as the spec and its answered / superseded / timed-out flags as
 * ``resolution``.
 */

export const LEGACY_KINDS = Object.freeze([
  "card",
  "ask_text",
  "confirm",
  "selection",
  "progress",
  "notification",
])

export function legacyRendererId(kind) {
  return `kt.${kind}`
}

/** Legacy kind of a renderer id, or ``null`` when it is not a legacy id. */
export function legacyKindOf(renderer) {
  if (typeof renderer !== "string" || !renderer.startsWith("kt.")) return null
  const kind = renderer.slice(3)
  return LEGACY_KINDS.includes(kind) ? kind : null
}

export function widgetFromLegacyMessage(message) {
  const resolution = {
    replied: message.replied === true,
    superseded: message.superseded === true,
    timedOut: message.timedOut === true,
    repliedActionId: message.repliedActionId || "",
    repliedValues: message.repliedValues || null,
  }
  return {
    id: message.eventId,
    renderer: legacyRendererId(message.uiEventType),
    version: 1,
    title: "",
    spec: message.payload || {},
    state: {},
    surface: "chat",
    interactive: message.interactive === true,
    awaiting:
      message.interactive === true &&
      !(resolution.replied || resolution.superseded || resolution.timedOut),
    closed: resolution.replied || resolution.superseded || resolution.timedOut,
    fallback: "",
    resolution,
  }
}

export function widgetFromMessage(message) {
  if (message.role === "ui_event") return widgetFromLegacyMessage(message)
  return {
    id: message.eventId,
    renderer: message.renderer || "",
    version: message.version || 1,
    title: message.title || "",
    spec: message.spec ?? null,
    state: message.state || {},
    surface: message.surfaceOverride || message.surface || "chat",
    interactive: message.interactive === true,
    awaiting: message.awaiting === true && !message.closed,
    closed: message.closed === true,
    fallback: message.fallback || "",
    lastAck: message.lastAck || null,
  }
}
