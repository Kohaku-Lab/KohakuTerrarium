import {
  computed,
  effectScope,
  hasInjectionContext,
  inject,
  onScopeDispose,
  ref,
  shallowRef,
  toValue,
  watch,
} from "vue"

import { acquireScope, releaseScope } from "@/composables/useScope"
import { subscribeAttention, workForScope } from "@/stores/attention"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { useInstancesStore } from "@/stores/instances"
import { getRuntimeScope } from "@/stores/runtimeScope"
import { useTabsStore } from "@/stores/tabs"
import { contextRealm, desktopContextConnected, desktopWork } from "./desktopContext"
import { useWorkSpeech } from "./workSpeech"

export const WORK_CONTEXT_KEY = Symbol("kt-work-context")
const trackers = new WeakMap()

/** Retain the last selected live tab within one runtime identity. */
export function startWorkContextTracking() {
  const tabs = useTabsStore()
  if (trackers.has(tabs)) return trackers.get(tabs)
  const last = ref(null)
  const scope = effectScope(true)
  let owner
  scope.run(() =>
    watch(
      () => ({
        host: JSON.stringify([contextRealm(), getRuntimeScope()]),
        id: tabs.activeId,
        live: tabs.activeTab?.kind === "attach" && !!tabs.activeTab.target,
        retained: last.value && tabs.byId[last.value],
      }),
      ({ host, id, live, retained }) => {
        const switched = owner !== undefined && host !== owner
        owner = host
        if (switched || !retained) last.value = null
        if (!switched && live) last.value = id
      },
      { immediate: true, flush: "sync" },
    ),
  )
  const tracker = {
    last,
    stop() {
      scope.stop()
      trackers.delete(tabs)
    },
  }
  trackers.set(tabs, tracker)
  return tracker
}

/** Selected desktop work, an explicit session scope, or a retained/pinned conversation. */
export function useWorkContext(options = {}, label = "") {
  const {
    retainLast = false,
    includeSpeech = false,
    target,
    enabled = true,
    scope = null,
    local = false,
  } = typeof options === "string" || options === null ? { scope: options } : options
  const injected = hasInjectionContext() ? inject(WORK_CONTEXT_KEY, null) : null
  const tabs = useTabsStore()
  const hosts = useHostsStore()
  const instances = useInstancesStore()
  const tracker = retainLast && target === undefined && !scope ? startWorkContextTracking() : null
  const explicit = target === undefined ? null : observeTarget(target, enabled)
  const tab = computed(() =>
    explicit
      ? { target: toValue(target)?.sessionId, label: toValue(target)?.label }
      : scope
        ? { kind: "attach", target: scope, label }
        : tracker
          ? tabs.byId[tracker.last.value]
          : tabs.activeTab,
  )
  const revision = ref(0)
  onScopeDispose(subscribeAttention(() => revision.value++))
  const binding = computed(() => {
    if (!toValue(enabled)) return null
    if (explicit) return explicit.value?.chat || null
    if (tab.value?.kind !== "attach" || !tab.value.target) return null
    const chat = useChatStore(tab.value.target)
    return chat._instanceHostId === hosts.activeHostId &&
      chat._instanceRuntimeScope === getRuntimeScope()
      ? chat
      : null
  })
  const source = computed(() =>
    explicit ? explicit.value?.source : binding.value?.terrariumTarget,
  )
  const speech = includeSpeech ? useWorkSpeech(binding, source) : null
  return computed(() => {
    revision.value
    if (!toValue(enabled)) return null
    if (!local && target === undefined && !scope && (injected || desktopContextConnected.value)) {
      const value = injected ? injected.value : desktopWork.value
      if (!value || (!retainLast && value.retained)) return null
      const { speech: remoteSpeech, ...work } = value
      return Object.freeze({ ...work, ...(includeSpeech ? { speech: remoteSpeech } : {}) })
    }
    const chat = binding.value
    if (!chat) return null
    const current = tab.value
    const instance = instances.list.find(
      (item) => item.id === current.target || item.graph_id === current.target,
    )
    const creature = instance?.creatures?.find(
      (item) =>
        item.creature_id === source.value ||
        item.name === source.value ||
        (source.value === "root" && item.is_root),
    )
    return Object.freeze({
      hostId: chat._instanceHostId,
      sessionId: current.target,
      creatureId: creature?.creature_id || source.value || "",
      creatureLabel: creature?.name || source.value || "",
      label: current.label || current.config_name || current.target,
      connected: chat.wsStatus === "open",
      ...(tracker ? { retained: tabs.activeId !== tracker.last.value } : {}),
      ...workForScope(current.target),
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
      contextRealm,
      getRuntimeScope,
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
    if (
      !value ||
      value.chat._instanceHostId !== hosts.activeHostId ||
      value.chat._instanceRuntimeScope !== getRuntimeScope()
    )
      return null
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
