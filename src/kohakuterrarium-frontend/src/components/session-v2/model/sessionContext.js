/**
 * The v2 session context: one object the shell provides and every v2
 * component injects — the instance, its scoped chat store, the active
 * session tab, the open floating widget and the side view.
 *
 * - `openWidget(id)` / `closeWidget()`: one floating widget at a time.
 * - `openSide(kind, payload)` / `closeSide()`: the side view beside the
 *   chat column (opening one closes the floating widget); `pinWidget(id)`
 *   moves the open widget there.
 * - `setTab(id)`: switches session tab; per-session, remembered locally.
 * - `openAdd(kind)` / `closeAdd()`: the "Add to session" dialog, opened on
 *   one of its kinds (creature, inline, terrarium, channel).
 * - `refresh()`: reloads the shown instance after a mutation.
 * - `focused`: whether the shell takes keyboard shortcuts such as Esc.
 * - `column`: chat-column state that outlives one mounted column (Chat and
 *   Workspace each mount their own): composer attachments, per-conversation
 *   scroll offsets, and whether the canvas has auto-opened.
 */

import { computed, inject, provide, ref, shallowRef } from "vue"

import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

import { SESSION_TABS } from "./sessionModel"

const KEY = Symbol("kt-session-v2")

export function createSessionV2({
  instance,
  instanceId,
  chat,
  focused = ref(true),
  refresh = null,
}) {
  const tabKey = () => `kt.v2.tab.${instanceId.value}`
  const stored = readLocalPref(tabKey())
  const tab = ref(SESSION_TABS.some((s) => s.id === stored) ? stored : "chat")
  const widget = ref(null)
  const side = shallowRef(null)
  const addKind = ref(null)
  const column = { attachments: shallowRef([]), scrollPositions: new Map(), canvasAutoOpened: null }

  function setTab(id) {
    tab.value = id
    writeLocalPref(tabKey(), id)
  }

  return {
    instance,
    instanceId,
    chat,
    focused,
    sessionId: computed(() => instance.value?.graph_id || instance.value?.id || instanceId.value),
    /** Reload the shown instance after a mutation; failures are swallowed (the poll retries). */
    async refresh() {
      try {
        await refresh?.()
      } catch {
        /* the shell's own poll retries */
      }
    },
    tab,
    widget,
    side,
    column,
    setTab,
    openWidget(id) {
      widget.value = widget.value === id ? null : id
    },
    closeWidget() {
      widget.value = null
    },
    openSide(kind, payload = {}) {
      widget.value = null
      side.value = { kind, payload }
      if (tab.value !== "chat") setTab("chat")
    },
    closeSide() {
      side.value = null
    },
    pinWidget(id) {
      widget.value = null
      side.value = { kind: "widget", payload: { id } }
    },
    addKind,
    openAdd(kind = "creature") {
      addKind.value = kind
    },
    closeAdd() {
      addKind.value = null
    },
  }
}

export function provideSessionV2(ctx) {
  provide(KEY, ctx)
  return ctx
}

export function useSessionV2() {
  const ctx = inject(KEY, null)
  if (!ctx) throw new Error("useSessionV2() used outside the v2 session shell")
  return ctx
}
