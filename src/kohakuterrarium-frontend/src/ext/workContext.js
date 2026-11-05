import { computed, effectScope, onUnmounted, ref, watch } from "vue"

import { subscribeAttention, workForScope } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useTabsStore } from "@/stores/tabs"
import { useWorkSpeech } from "./workSpeech"

const trackers = new WeakMap()

/** Track the last focused live tab for the lifetime of the extension host. */
export function startWorkContextTracking() {
  const tabs = useTabsStore()
  if (trackers.has(tabs)) return trackers.get(tabs)
  const hosts = useHostsStore()
  const last = ref(null)
  const scope = effectScope(true)
  let owner
  scope.run(() =>
    watch(
      () => {
        const host = `${hosts.activeHostId}|${hosts.activeHost?.currentUser?.id ?? ""}`
        const tab = tabs.activeTab
        return {
          host,
          id: tabs.activeId,
          live: tab?.kind === "attach" && !!tab.target,
          retained: last.value && tabs.byId[last.value],
        }
      },
      ({ host, id, live, retained }) => {
        const switched = owner !== undefined && host !== owner
        owner = host
        if (switched || !retained) last.value = null
        if (switched) return
        if (live) last.value = id
      },
      { immediate: true, flush: "sync" },
    ),
  )
  const tracker = {
    last,
    stop: () => {
      scope.stop()
      trackers.delete(tabs)
    },
  }
  trackers.set(tabs, tracker)
  return tracker
}

/** Read-only observed work, optionally retained while visiting non-session tabs. */
export function useWorkContext({ retainLast = false, includeSpeech = false } = {}) {
  const tabs = useTabsStore()
  const hosts = useHostsStore()
  const tracker = retainLast ? startWorkContextTracking() : null
  const revision = ref(0)
  const off = subscribeAttention(() => revision.value++)
  onUnmounted(off)
  const binding = computed(() => {
    const tab = tracker ? tabs.byId[tracker.last.value] : tabs.activeTab
    if (tab?.kind !== "attach" || !tab.target) return null
    const chat = useChatStore(tab.target)
    return chat._instanceHostId === hosts.activeHostId ? chat : null
  })
  const speech = includeSpeech ? useWorkSpeech(binding) : null
  return computed(() => {
    revision.value
    const chat = binding.value
    if (!chat) return null
    const tab = tracker ? tabs.byId[tracker.last.value] : tabs.activeTab
    return Object.freeze({
      hostId: chat._instanceHostId,
      sessionId: tab.target,
      creatureId: chat.terrariumTarget || "",
      label: tab.label || tab.config_name || tab.target,
      connected: chat.wsStatus === "open",
      ...workForScope(tab.target),
      ...(speech ? { speech: speech.value } : {}),
    })
  })
}
