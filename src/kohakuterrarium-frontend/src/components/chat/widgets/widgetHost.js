/**
 * The ``host`` object a widget renderer receives: messages and state go to
 * the creature that opened the widget through the chat store, ``theme`` is
 * the dashboard's live tokens, and ``openInCanvas`` moves the widget to the
 * canvas of the same scope.
 */

import { widgetTheme } from "./widgetTheme.js"

export function findWidgetMessage(store, eventId, tab = "") {
  const tabs = tab ? [tab] : Object.keys(store?.messagesByTab || {})
  for (const name of tabs) {
    const found = (store?.messagesByTab?.[name] || []).find(
      (m) => m.role === "widget" && m.eventId === eventId,
    )
    if (found) return { message: found, tab: name }
  }
  return null
}

export function makeWidgetHost({ store, tab, eventId, canvas = null, extra = {} }) {
  const resolveTab = () => (typeof tab === "function" ? tab() : tab)
  return {
    send: (name, data) => store.submitWidgetMessage(resolveTab(), eventId, name, data || {}),
    setState: (patch) => store.setWidgetState(resolveTab(), eventId, patch),
    close: () => {},
    openInCanvas: () => {
      const found = findWidgetMessage(store, eventId, resolveTab())
      if (!found || !canvas) return
      found.message.surfaceOverride = "canvas"
      canvas.pinWidget(found.message)
    },
    theme: widgetTheme(),
    ...extra,
  }
}
