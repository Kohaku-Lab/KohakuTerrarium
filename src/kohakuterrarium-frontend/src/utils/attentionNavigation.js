import { useChatStore } from "@/stores/chat"
import { useTabsStore } from "@/stores/tabs"

export function navigateToAttention({ scope, tab }) {
  if (!scope || !tab) return false

  const tabs = useTabsStore()
  const id = `attach:${scope}`
  tabs.openTab({ kind: "attach", id, target: scope })
  tabs.activateTab(id)
  useChatStore(scope).openTab(tab)
  return true
}
