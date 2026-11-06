import { computed, effectScope, onScopeDispose, ref, shallowRef, toValue, watch } from "vue"

import { acquireScope, releaseScope } from "@/composables/useScope"
import { subscribeAttention, workForScope } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"
import { useWorkSpeech } from "./workSpeech"

const trackers = new WeakMap()

/** Track the last focused live tab for the lifetime of the extension host. */
export function startWorkContextTracking() {
  const tabs = useTabsStore()
  if (trackers.has(tabs)) return trackers.get(tabs)
  const hosts = useHostsStore()
  const last = ref(null)
  const remote = shallowRef(null)
  let remoteWindow = null
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
        if (switched) {
          remote.value = null
          remoteWindow = null
        }
        if (switched) return
        if (live) {
          last.value = id
          if (document.hasFocus()) {
            remote.value = null
            remoteWindow = null
          }
        }
      },
      { immediate: true, flush: "sync" },
    ),
  )
  const focus = () => {
    if (tabs.activeTab?.kind === "attach") {
      remote.value = null
      remoteWindow = null
      last.value = tabs.activeId
    }
  }
  const receive = ({ detail }) => {
    const host = `${hosts.activeHostId || ""}|${hosts.activeHost?.currentUser?.id ?? ""}`
    if (!detail || typeof detail.window !== "string") return
    if (detail.closed) {
      if (remoteWindow === detail.window) {
        remote.value = null
        remoteWindow = null
        last.value = null
      }
      return
    }
    if (detail.host !== host) return
    if (detail.selected && detail.work) remoteWindow = detail.window
    if (remoteWindow === detail.window) remote.value = detail.work
  }
  window.addEventListener("focus", focus)
  window.addEventListener("kt:work-context", receive)
  const tracker = {
    last,
    remote,
    stop: () => {
      window.removeEventListener("focus", focus)
      window.removeEventListener("kt:work-context", receive)
      scope.stop()
      trackers.delete(tabs)
    },
  }
  trackers.set(tabs, tracker)
  return tracker
}

/** Read-only observed work, optionally retained while visiting non-session tabs. */
export function useWorkContext({
  retainLast = false,
  includeSpeech = false,
  target,
  enabled = true,
} = {}) {
  const tabs = useTabsStore()
  const hosts = useHostsStore()
  const instances = useInstancesStore()
  const tracker = retainLast && target === undefined ? startWorkContextTracking() : null
  const explicit = target === undefined ? null : observeTarget(target, enabled)
  const revision = ref(0)
  const off = subscribeAttention(() => revision.value++)
  onScopeDispose(off)
  const binding = computed(() => {
    if (!toValue(enabled)) return null
    if (explicit) return explicit.value?.chat || null
    const tab = tracker ? tabs.byId[tracker.last.value] : tabs.activeTab
    if (tab?.kind !== "attach" || !tab.target) return null
    const chat = useChatStore(tab.target)
    return chat._instanceHostId === hosts.activeHostId ? chat : null
  })
  const source = computed(() =>
    explicit ? explicit.value?.source : binding.value?.terrariumTarget,
  )
  const speech = includeSpeech ? useWorkSpeech(binding, source) : null
  return computed(() => {
    revision.value
    if (tracker?.remote.value && toValue(enabled)) {
      const { speech: remoteSpeech, ...work } = tracker.remote.value
      return Object.freeze({ ...work, ...(includeSpeech ? { speech: remoteSpeech } : {}) })
    }
    const chat = binding.value
    if (!chat) return null
    const tab = explicit
      ? { target: toValue(target)?.sessionId, label: toValue(target)?.label }
      : tracker
        ? tabs.byId[tracker.last.value]
        : tabs.activeTab
    const instance = instances.list.find(
      (item) => item.id === tab.target || item.graph_id === tab.target,
    )
    const creature = instance?.creatures?.find(
      (item) =>
        item.creature_id === source.value ||
        item.name === source.value ||
        (source.value === "root" && item.is_root),
    )
    return Object.freeze({
      hostId: chat._instanceHostId,
      sessionId: tab.target,
      creatureId: creature?.creature_id || source.value || "",
      creatureLabel: creature?.name || source.value || "",
      label: tab.label || tab.config_name || tab.target,
      connected: chat.wsStatus === "open",
      ...workForScope(tab.target),
      ...(speech ? { speech: speech.value } : {}),
    })
  })
}

function observeTarget(target, enabled) {
  const current = shallowRef(null)
  const hosts = useHostsStore()
  const instances = useInstancesStore()
  const stop = watch(
    [
      () => toValue(enabled),
      () => toValue(target)?.sessionId,
      () => hosts.activeHostId,
      () => hosts.activeHost?.currentUser?.id,
    ],
    ([active, sessionId], _, cleanup) => {
      current.value = null
      if (!active || !sessionId) return
      let disposed = false
      let retry
      acquireScope(sessionId)
      cleanup(() => {
        disposed = true
        clearTimeout(retry)
        releaseScope(sessionId)
      })
      async function connect() {
        try {
          const instance = await instances.fetchOne(sessionId)
          if (disposed || !instance) return
          const chat = useChatStore(sessionId)
          chat.initForInstance(instance)
          current.value = { chat, instance }
        } catch {
          // A transient fetch failure must not require reselecting the target.
        } finally {
          if (!disposed && !current.value) retry = setTimeout(connect, 5000)
        }
      }
      void connect()
    },
    { immediate: true, flush: "sync" },
  )
  onScopeDispose(stop)
  return computed(() => {
    const value = current.value
    if (!value) return null
    const { sessionId, creatureId } = toValue(target) || {}
    const instance =
      instances.list.find((item) => item.id === sessionId || item.graph_id === sessionId) ||
      value.instance
    const creature = instance.creatures?.find(
      (item) =>
        item.creature_id === creatureId ||
        item.name === creatureId ||
        (creatureId === "root" && item.is_root),
    )
    if (creatureId && !creature) return null
    return {
      chat: value.chat,
      source: creature ? (creature.is_root ? "root" : creature.name) : value.chat.terrariumTarget,
    }
  })
}
