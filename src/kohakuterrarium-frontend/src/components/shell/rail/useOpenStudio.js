/**
 * Opening Studio from the shell: the open workspace's tab when one is open,
 * else the workspace picker. Read-only use of the Studio workspace store.
 */

import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useTabsStore } from "@/stores/tabs"
import { buildStudioTabId } from "@/utils/tabsUrl"

export function useOpenStudio() {
  const tabs = useTabsStore()
  const ws = useStudioWorkspaceStore()
  return function openStudio() {
    if (ws.isOpen && ws.root) {
      tabs.openTab({
        kind: "studio-editor",
        id: buildStudioTabId({ entityKind: "workspace", workspace: ws.root }),
        workspace: ws.root,
        entity: ws.root,
        entityKind: "workspace",
      })
      return
    }
    tabs.openTab({
      kind: "studio-editor",
      id: buildStudioTabId({ entityKind: "home" }),
      workspace: "",
      entity: "home",
      entityKind: "home",
    })
  }
}
