/**
 * Opening workspaces in the Studio app: the remembered one (the local
 * project by default) when Studio starts, and the one the user switches to.
 */

import { ElMessage } from "element-plus"

import {
  PROJECT_REF,
  goStudio,
  rememberWorkspace,
  useStudioRoute,
} from "@/components/studio/app/useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useI18n } from "@/utils/i18n"

export function useStudioWorkspace() {
  const ws = useStudioWorkspaceStore()
  const { workspaceRef } = useStudioRoute()
  const { t } = useI18n()

  /** Open `target` (a folder or "@"), remember it, and show its overview. */
  async function openWorkspace(target, { quiet = false } = {}) {
    try {
      const summary = await ws.open(target)
      rememberWorkspace(summary.is_project ? PROJECT_REF : summary.root)
      goStudio({ view: "overview" })
      return summary
    } catch (err) {
      if (!quiet)
        ElMessage.error(
          t("studioApp.ws.failed", { path: target, message: err?.message || String(err) }),
        )
      throw err
    }
  }

  /** Make sure a workspace is open: the server's, else the remembered one, else the local project. */
  async function ensureWorkspace() {
    await ws.hydrate()
    if (ws.isOpen) return ws.summary
    const remembered = workspaceRef.value || PROJECT_REF
    try {
      return await ws.open(remembered)
    } catch (err) {
      if (remembered === PROJECT_REF) throw err
      const summary = await ws.open(PROJECT_REF)
      rememberWorkspace(PROJECT_REF)
      goStudio({ view: "overview" })
      return summary
    }
  }

  return { ws, openWorkspace, ensureWorkspace }
}
