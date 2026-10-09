/**
 * The v2 session context: one object the shell provides and every v2
 * component injects — the instance, its scoped chat store, the active
 * session tab, the open floating widget and the side view.
 *
 * - `openWidget(id)` / `closeWidget()`: one floating widget at a time.
 * - `openSide(kind, payload)` / `closeSide()`: the side view beside the
 *   chat column (opening one closes the floating widget and, off `phone`,
 *   shows the Chat tab; phones show it full screen over any tab);
 *   `pinWidget(id)` moves the open widget there.
 * - `setTab(id)`: switches session tab; per-session, remembered locally.
 *   Phones have no Workspace tab: picking it there is ignored, and a
 *   remembered Workspace shows Chat until the screen is wide again.
 * - `openAdd(kind)` / `closeAdd()`: the "Add to session" dialog, opened on
 *   one of its kinds (creature, inline, terrarium, channel).
 * - `openSheet(kind, payload)` / `closeSheet()`: the one phone bottom sheet
 *   (conversations, menu, model, agent), `sheet` holding {kind, payload}.
 * - `refresh()`: reloads the shown instance after a mutation.
 * - `focused`: whether the shell takes keyboard shortcuts such as Esc.
 * - `column`: chat-column state that outlives one mounted column (Chat and
 *   Workspace each mount their own): composer attachments, per-conversation
 *   scroll offsets, whether the canvas has auto-opened, and the shown
 *   column's `actions` ({compact, clear}) for the phone session menu.
 */

import { computed, inject, provide, ref, shallowRef } from "vue"

import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

import { SESSION_TABS, sessionTabs } from "./sessionModel"

const KEY = Symbol("kt-session-v2")

export function createSessionV2({
  instance,
  instanceId,
  chat,
  focused = ref(true),
  refresh = null,
  phone = ref(false),
}) {
  const tabKey = () => `kt.v2.tab.${instanceId.value}`
  const stored = readLocalPref(tabKey())
  const chosen = ref(SESSION_TABS.some((s) => s.id === stored) ? stored : "chat")
  // A desktop-only pick (Workspace) shows Chat on a phone and comes back on a wider screen.
  const tab = computed(() =>
    sessionTabs(phone.value).some((s) => s.id === chosen.value) ? chosen.value : "chat",
  )
  const widget = ref(null)
  const side = shallowRef(null)
  const addKind = ref(null)
  const sheet = shallowRef(null)
  const column = {
    attachments: shallowRef([]),
    scrollPositions: new Map(),
    canvasAutoOpened: null,
    actions: shallowRef(null),
  }

  function setTab(id) {
    if (!sessionTabs(phone.value).some((s) => s.id === id)) return
    chosen.value = id
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
      sheet.value = null
      widget.value = widget.value === id ? null : id
    },
    closeWidget() {
      widget.value = null
    },
    openSide(kind, payload = {}) {
      widget.value = null
      sheet.value = null
      side.value = { kind, payload }
      if (!phone.value && tab.value !== "chat") setTab("chat")
    },
    closeSide() {
      side.value = null
    },
    pinWidget(id) {
      widget.value = null
      side.value = { kind: "widget", payload: { id } }
    },
    sheet,
    openSheet(kind, payload = {}) {
      sheet.value = { kind, payload }
    },
    closeSheet() {
      sheet.value = null
    },
    addKind,
    openAdd(kind = "creature") {
      sheet.value = null
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
