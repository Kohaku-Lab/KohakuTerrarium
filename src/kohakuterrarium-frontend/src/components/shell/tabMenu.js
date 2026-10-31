import { actionMenuItems } from "@/ext/actions"

/**
 * Rows of a tab's context menu, as the native menu draws them: the built-in commands, then the
 * `tab` actions extensions contribute.
 */
export function tabMenuItems({ tab, index, total, canPopOut, t }) {
  const isDashboard = tab.id === "dashboard"
  const items = [{ id: "refresh", label: t("shell.tab.refresh") }]
  if (canPopOut) items.push({ id: "move", label: t("shell.tab.moveToWindow") })
  items.push(
    { separator: true },
    { id: "close", label: t("shell.tab.close"), enabled: !isDashboard },
    { id: "close-left", label: t("shell.tab.closeLeft"), enabled: index > 0 },
    {
      id: "close-right",
      label: t("shell.tab.closeRight"),
      enabled: index >= 0 && index < total - 1,
    },
    { id: "close-others", label: t("shell.tab.closeOthers"), enabled: total > 1 },
    { id: "close-all", label: t("shell.tab.closeAll") },
  )
  const extensions = actionMenuItems("tab", {
    id: tab.id,
    kind: tab.kind || "",
    label: tab.label || "",
  })
  if (extensions.length)
    items.push({ separator: true }, ...extensions.map(({ id, label }) => ({ id, label })))
  return items
}
