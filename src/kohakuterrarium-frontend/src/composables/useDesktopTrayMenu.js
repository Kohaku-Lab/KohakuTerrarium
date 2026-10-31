import { onBeforeUnmount, onMounted, watch } from "vue"

import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"
import { subscribeAttention, waitingAttentionTargets } from "@/stores/attention"
import { useInstancesStore } from "@/stores/instances"
import {
  attentionTargetLabel,
  navigateToAttention,
  openSessionScope,
} from "@/utils/attentionNavigation"
import { hasDesktopHost, onHostReady, pickFolderNative, setTraySessions } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

export const TRAY_ACTION_EVENT = "kt:desktop-tray-action"
const PUSH_DELAY_MS = 250

/** The tray's session entries: sessions waiting on the user, then every other listed session. */
export function buildTrayModel(targets, instances, labelOf) {
  const waiting = []
  for (const target of targets) {
    if (waiting.some((entry) => entry.id === target.scope)) continue
    waiting.push({ id: target.scope, label: labelOf(target) })
  }
  const sessions = []
  for (const instance of instances) {
    const id = instance.graph_id || instance.id
    const label = instance.session_name || instance.config_name || id
    if (id && !sessions.some((entry) => entry.id === id)) sessions.push({ id, label })
  }
  return { waiting, sessions }
}

/** Keep the tray menu's session entries current and carry out what the user picks there. */
export function useDesktopTrayMenu() {
  const instances = useInstancesStore()
  const { t } = useI18n()
  let stopHostReady = () => {}
  let stopAttention = () => {}
  let stopWatch = () => {}
  let timer = null
  let sent = ""

  function model() {
    return buildTrayModel(waitingAttentionTargets(), instances.list, attentionTargetLabel)
  }

  function push() {
    timer = null
    const next = model()
    const key = JSON.stringify(next)
    if (key === sent) return
    sent = key
    void setTraySessions(next)
  }

  function schedule() {
    if (timer === null && hasDesktopHost()) timer = setTimeout(push, PUSH_DELAY_MS)
  }

  async function onAction(event) {
    const { kind, id } = event.detail || {}
    if (kind === "new") {
      window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path: "" } }))
    } else if (kind === "folder") {
      const path = await pickFolderNative({ title: t("studio.picker.title") })
      if (path) window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path } }))
    } else if (kind === "session") {
      const target = waitingAttentionTargets().find((entry) => entry.scope === id)
      if (!target || !navigateToAttention(target)) openSessionScope(id)
    }
  }

  onMounted(() => {
    stopHostReady = onHostReady(schedule)
    stopAttention = subscribeAttention(schedule)
    stopWatch = watch(
      () =>
        instances.list.map((it) => `${it.graph_id || it.id}:${it.session_name || it.config_name}`),
      schedule,
    )
    window.addEventListener(TRAY_ACTION_EVENT, onAction)
  })
  onBeforeUnmount(() => {
    stopHostReady()
    stopAttention()
    stopWatch()
    if (timer !== null) clearTimeout(timer)
    window.removeEventListener(TRAY_ACTION_EVENT, onAction)
  })
}
