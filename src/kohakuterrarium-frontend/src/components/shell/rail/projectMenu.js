import { isRemoteProject } from "@/stores/projects"

/**
 * Why a new session cannot start in `project` from here ("" when it can): its
 * folder is gone, or it lives on another node.
 */
export function newHereBlocker(project, t) {
  if (project?.exists === false) return t("lab.rail.projects.newHereMissing")
  if (isRemoteProject(project)) return t("lab.rail.projects.newHereRemote", { node: project.node })
  return ""
}

/**
 * Rows of a project's menu, in the shape the native menu draws
 * (`{ id, label, enabled?, hint? }` or `{ separator: true }`).
 */
export function projectMenuItems(project, t) {
  const blocker = newHereBlocker(project, t)
  const items = [
    { id: "new-session", label: t("lab.rail.projects.newHere"), enabled: !blocker, hint: blocker },
    { id: "open-history", label: t("lab.rail.projects.openHistory") },
    { separator: true },
    { id: "rename", label: t("lab.rail.projects.rename") },
    project.pinned
      ? { id: "unpin", label: t("lab.rail.projects.unpin") }
      : { id: "pin", label: t("lab.rail.projects.pin") },
    project.hidden
      ? { id: "unhide", label: t("lab.rail.projects.unhide") }
      : { id: "hide", label: t("lab.rail.projects.hide") },
  ]
  if (project.saved) items.push({ id: "forget", label: t("lab.rail.projects.forget") })
  return items
}
