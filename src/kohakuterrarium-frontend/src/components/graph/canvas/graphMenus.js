/**
 * Context-menu items for every graph target (creature, channel, edge,
 * group, empty canvas). Items depend on the target's live state; the
 * surface dispatches the chosen id. Pure.
 */

const divider = (id) => ({ id, divider: true })

function creatureItems(c, t) {
  const items = [
    { id: "chat", label: t("graph.action.chat"), icon: "i-carbon-chat" },
    { id: "open-tab", label: t("graph.action.openTab"), icon: "i-carbon-launch" },
    { id: "inspect", label: t("graph.action.inspect"), icon: "i-carbon-search-locate" },
    divider("d1"),
  ]
  if (c.status === "busy")
    items.push({
      id: "interrupt",
      label: t("graph.action.interrupt"),
      icon: "i-carbon-stop-outline",
    })
  if (c.status === "stopped")
    items.push({ id: "start", label: t("graph.action.start"), icon: "i-carbon-play" })
  else items.push({ id: "stop", label: t("graph.action.stop"), icon: "i-carbon-pause" })
  items.push(
    divider("d2"),
    { id: "new-channel-from", label: t("graph.menu.newChannelFrom"), icon: "i-carbon-flow-stream" },
    { id: "new-creature-wired", label: t("graph.menu.newCreatureWired"), icon: "i-carbon-bot" },
    { id: "focus", label: t("graph.menu.focus"), icon: "i-carbon-center-circle" },
    divider("d3"),
    {
      id: "remove",
      label: t("graph.action.removeCreature"),
      icon: "i-carbon-trash-can",
      danger: true,
    },
  )
  return items
}

function channelItems(t) {
  return [
    { id: "chat", label: t("graph.menu.openChannel"), icon: "i-carbon-chat" },
    { id: "open-tab", label: t("graph.action.openTab"), icon: "i-carbon-launch" },
    { id: "details", label: t("graph.menu.post"), icon: "i-carbon-send" },
    { id: "focus", label: t("graph.menu.focus"), icon: "i-carbon-center-circle" },
    divider("d1"),
    {
      id: "remove",
      label: t("graph.action.removeChannel"),
      icon: "i-carbon-trash-can",
      danger: true,
    },
  ]
}

function edgeItems(e, t) {
  const editable = (e.count || 1) === 1 && (e.kind === "wire" || e.kind === "channel")
  const items = [{ id: "details", label: t("graph.menu.edit"), icon: "i-carbon-edit" }]
  if (editable && e.kind === "wire")
    items.push(
      {
        id: "wire-payload",
        label: e.withContent === false ? t("graph.edge.content") : t("graph.edge.ping"),
        icon: e.withContent === false ? "i-carbon-document" : "i-carbon-notification",
      },
      { id: "wire-reverse", label: t("graph.edge.reverse"), icon: "i-carbon-arrows-horizontal" },
    )
  items.push({
    id: "remove",
    label: t("graph.action.disconnect"),
    icon: "i-carbon-unlink",
    danger: true,
    disabled: !editable,
  })
  return items
}

function groupItems(g, t) {
  const items = [
    {
      id: "toggle-collapse",
      label: g.collapsed ? t("graph.group.expand") : t("graph.group.collapse"),
      icon: g.collapsed ? "i-carbon-expand-categories" : "i-carbon-collapse-categories",
    },
  ]
  if (g.kind === "session") {
    items.push(
      { id: "focus-session", label: t("graph.action.focusSession"), icon: "i-carbon-zoom-in-area" },
      { id: "open-tab", label: t("graph.action.openTab"), icon: "i-carbon-launch" },
      divider("d1"),
      {
        id: "stop-session",
        label: t("graph.action.stopSession"),
        icon: "i-carbon-power",
        danger: true,
      },
    )
  }
  return items
}

function paneItems(view, t) {
  const disabled = view.isSample || !view.model.sessions.length
  return [
    { id: "add-creature", label: t("graph.action.addCreature"), icon: "i-carbon-bot", disabled },
    {
      id: "add-channel",
      label: t("graph.action.addChannel"),
      icon: "i-carbon-flow-stream",
      disabled,
    },
    divider("d1"),
    { id: "fit", label: t("graph.toolbar.fit"), icon: "i-carbon-fit-to-screen" },
    { id: "relayout", label: t("graph.toolbar.relayout"), icon: "i-carbon-renew" },
  ]
}

/** Items for `target` = {kind, item}; returns [] for unknown targets. */
export function menuItemsFor(target, view, t) {
  switch (target?.kind) {
    case "creature":
      return creatureItems(target.item, t)
    case "channel":
      return channelItems(t)
    case "edge":
      return edgeItems(target.item, t)
    case "group":
      return groupItems(target.item, t)
    case "pane":
      return paneItems(view, t)
    default:
      return []
  }
}
