import { useInstancesStore } from "@/stores/instances"
import { usePaletteStore } from "@/stores/palette"
import { useTabsStore } from "@/stores/tabs"
import { popoutWindowsSupported } from "@/utils/hostBridge"
import { popOutSession } from "@/utils/popout"

export const POPOUT_COMMAND_ID = "session:pop-out"

/** The session shown in the focused tab, as ``{ scope, title }``, or ``null`` when it is not a session. */
export function focusedSession() {
  const tabs = useTabsStore()
  const activeId = tabs.focusedGroup?.activeId
  const tab = activeId ? tabs.byId[activeId] : null
  if (tab?.kind !== "attach" || !tab.target) return null
  const instances = useInstancesStore()
  const match = (it) => it && (it.id === tab.target || it.graph_id === tab.target)
  const instance = match(instances.current) ? instances.current : instances.list.find(match)
  return { scope: tab.target, title: instance?.session_name || instance?.config_name || "" }
}

/** Pop the focused session out into its own window. Resolves ``false`` when there is nothing to pop out. */
export async function popOutFocusedSession() {
  const session = focusedSession()
  if (!session) return false
  return popOutSession(session.scope, session.title)
}

/**
 * Offer "pop out" in the command palette where the shell can open session windows.
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
}
