import { computed, onUnmounted, ref } from "vue"

import { subscribeAttention, workForScope } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useTabsStore } from "@/stores/tabs"

/** Read-only observed work of the focused live session, or null outside one. */
export function useWorkContext() {
  const tabs = useTabsStore()
  const hosts = useHostsStore()
  const revision = ref(0)
  const off = subscribeAttention(() => revision.value++)
  onUnmounted(off)
  return computed(() => {
    revision.value
    const tab = tabs.activeTab
    if (tab?.kind !== "attach" || !tab.target) return null
    const hostId = useChatStore(tab.target)._instanceHostId
    if (hostId !== hosts.activeHostId) return null
    return Object.freeze({
      hostId,
      sessionId: tab.target,
      label: tab.label || tab.target,
      ...workForScope(tab.target),
    })
  })
}
