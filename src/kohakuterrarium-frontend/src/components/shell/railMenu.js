import { actionMenuItems } from "@/ext/actions"

/**
 * Rows of a rail item's context menu, as the native menu draws them: the built-in commands, then
 * the `rail-item` actions extensions contribute.
 */
export function railMenuItems({ instance, chatOpen, inspectorOpen, canPopOut, t }) {
  const items = [
    {
      id: "toggle-chat",
      label: chatOpen ? t("shell.rail.closeChat") : t("shell.rail.openChat"),
    },
    {
      id: "toggle-inspector",
      label: inspectorOpen ? t("shell.rail.closeInspector") : t("shell.rail.openInspector"),
    },
  ]
  if (canPopOut) items.push({ id: "open-in-window", label: t("shell.rail.openInWindow") })
  items.push(
    { separator: true },
    { id: "detach", label: t("shell.rail.detachAll"), enabled: chatOpen || inspectorOpen },
    { id: "end", label: t("shell.rail.endConversation") },
  )
  const extensions = actionMenuItems("rail-item", {
    id: instance.id ?? "",
    name: instance.config_name || "",
  })
  if (extensions.length)
    items.push({ separator: true }, ...extensions.map(({ id, label }) => ({ id, label })))
  return items
}
