/**
 * What the lab does to one running session: open its chat or inspector tab,
 * and stop it after the user confirms.
 */

import { ElMessage, ElMessageBox } from "element-plus"

import { useGraphLiveStore } from "@/stores/graph/live"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

export function useSessionActions() {
  const { t } = useI18n()
  const tabs = useTabsStore()
  const live = useGraphLiveStore()

  function openChat(session) {
    tabs.openSurface(session.id, "chat", { config_name: session.name })
  }

  function openInspector(session) {
    tabs.openSurface(session.id, "inspector", { config_name: session.name })
  }

  async function stop(session) {
    try {
      await ElMessageBox.confirm(
        t("graph.confirm.stopSessionBody", { n: session.size }),
        t("graph.confirm.stopSession", { name: session.name }),
        {
          type: "warning",
          confirmButtonText: t("graph.action.stopSession"),
          cancelButtonText: t("graph.action.cancel"),
        },
      )
    } catch {
      return
    }
    try {
      await sessionAPI.stopActive(session.id)
      await live.refresh()
    } catch (err) {
      ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
    }
  }

  return { openChat, openInspector, stop }
}
