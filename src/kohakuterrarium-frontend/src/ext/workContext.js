import { computed, inject, onUnmounted, ref } from "vue"

import { subscribeAttention, workForScope } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useTabsStore } from "@/stores/tabs"
import { getRuntimeScope } from "@/stores/runtimeScope"

/** Read-only observed work of the focused live session, or null outside one. */
export const WORK_CONTEXT_KEY = "kt:work-context"

export function useWorkContext(scope = null, label = "") {
  const relayed = inject(WORK_CONTEXT_KEY, null)
  if (relayed) return relayed
  const tabs = useTabsStore()
  const hosts = useHostsStore()
  const revision = ref(0)
  const off = subscribeAttention(() => revision.value++)
  onUnmounted(off)
  return computed(() => {
    revision.value
    const tab = scope ? { kind: "attach", target: scope, label } : tabs.activeTab
    if (tab?.kind !== "attach" || !tab.target) return null
    const chat = useChatStore(tab.target)
    const hostId = chat._instanceHostId
    if (hostId !== hosts.activeHostId || chat._instanceRuntimeScope !== getRuntimeScope())
      return null
    return Object.freeze({
      hostId,
      sessionId: tab.target,
      label: tab.label || tab.target,
      ...workForScope(tab.target),
    })
  })
}
