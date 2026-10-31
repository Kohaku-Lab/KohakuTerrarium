import { onBeforeUnmount, onMounted } from "vue"

import { useInstancesStore } from "@/stores/instances"
import { usePaletteStore } from "@/stores/palette"
import { useTabsStore } from "@/stores/tabs"
import { openSessionScope } from "@/utils/attentionNavigation"
import { popoutWindowsSupported } from "@/utils/hostBridge"
import { POPOUT_RETURN_EVENT, popOutSession } from "@/utils/popout"

export const POPOUT_COMMAND_ID = "session:pop-out"
/** Pixels between the pointer and the window's top-left corner when a tab is dragged out. */
const DRAG_OUT_OFFSET = { x: 120, y: 16 }

function sessionTitle(scope) {
  const instances = useInstancesStore()
  const match = (it) => it && (it.id === scope || it.graph_id === scope)
  const instance = match(instances.current) ? instances.current : instances.list.find(match)
  return instance?.session_name || instance?.config_name || ""
}

/** The session shown in the focused tab, as ``{ scope, title }``, or ``null`` when it is not a session. */
export function focusedSession() {
  const tabs = useTabsStore()
  const activeId = tabs.focusedGroup?.activeId
  const tab = activeId ? tabs.byId[activeId] : null
  if (tab?.kind !== "attach" || !tab.target) return null
  return { scope: tab.target, title: sessionTitle(tab.target) }
}

/** Pop the focused session out into its own window. Resolves ``false`` when there is nothing to pop out. */
export async function popOutFocusedSession() {
  const session = focusedSession()
  if (!session) return false
  return popOutSession(session.scope, session.title)
}

/** Show a listed session in a window of its own and leave it open here. */
export function popOutInstance(instance, position = null) {
  const scope = instance?.graph_id || instance?.id
  if (!scope) return Promise.resolve(false)
  return popOutSession(scope, instance.session_name || instance.config_name || "", position)
}

/** Move a session tab into a window of its own: the window opens first, then the tab closes. */
export async function moveTabToWindow(tab, position = null) {
  if (tab?.kind !== "attach" || !tab.target) return false
  const opened = await popOutSession(tab.target, sessionTitle(tab.target), position)
  if (opened) useTabsStore().closeTab(tab.id)
  return opened
}

/** Where a window dragged out at the pointer (`screenX`, `screenY`) should appear. */
export function dragOutPosition(event) {
  return { x: event.screenX - DRAG_OUT_OFFSET.x, y: event.screenY - DRAG_OUT_OFFSET.y }
}

/** Whether a drag that ended at ``event`` was dropped outside the page, where nothing took it. */
export function endedOutsideWindow(event, view = window) {
  if (event.dataTransfer?.dropEffect && event.dataTransfer.dropEffect !== "none") return false
  const { clientX: x, clientY: y } = event
  return x < 0 || y < 0 || x > view.innerWidth || y > view.innerHeight
}

/**
 * Offer "pop out" in the command palette where the shell can open session windows,
 * and show a session again when its window hands it back.
 * Registered once and not from an effect: the palette's other registrations run in
 * effects that read the command list, and two such effects would retrigger each other.
 */
export function useDesktopPopout() {
  if (!popoutWindowsSupported()) return
  usePaletteStore().register({
    id: POPOUT_COMMAND_ID,
    label: "Session: pop out into its own window",
    icon: "i-carbon-launch",
    keywords: "detach window separate",
    handler: () => void popOutFocusedSession(),
  })
  const onReturn = (event) => openSessionScope(event.detail?.scope)
  onMounted(() => window.addEventListener(POPOUT_RETURN_EVENT, onReturn))
  onBeforeUnmount(() => window.removeEventListener(POPOUT_RETURN_EVENT, onReturn))
}
