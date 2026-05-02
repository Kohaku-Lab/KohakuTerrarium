/**
 * useArtifactDetector — watches the chat store for assistant messages
 * and scans them for canvas artifacts.
 *
 * Scans periodically while processing (every 2s) to catch completed
 * code blocks mid-stream, plus a final scan when processing ends.
 *
 * **Scope.** Pass an explicit ``scope`` (the attach target id) when
 * calling from inside an ``AttachTab`` so this composable feeds the
 * scoped chat / canvas stores rather than the default singletons.
 * Vue 3's ``inject()`` does not see the caller's own ``provide()``,
 * so we can't rely on the in-composable ``useChatStore()``/``useCanvasStore()``
 * to auto-resolve when the caller is the same component that
 * provides scope. Calls without a ``scope`` argument fall through to
 * the default singletons — the v1 page-routed path that App.vue
 * still relies on.
 */

import { onUnmounted, watch } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"

export function useArtifactDetector(scope) {
  const chat = useChatStore(scope)
  const canvas = useCanvasStore(scope)
  let ctrl = null

  function scanAll() {
    const tab = chat.activeTab
    if (!tab) return
    const msgs = chat.messagesByTab?.[tab] || []
    for (const m of msgs) {
      canvas.scanMessage(m)
    }
  }

  // While processing, scan every 2s to catch completed code blocks
  // mid-stream. Visibility-aware so a backgrounded tab doesn't scan.
  watch(
    () => chat.processing,
    (processing) => {
      if (processing && !ctrl) {
        ctrl = createVisibilityInterval(scanAll, 2000)
        ctrl.start()
      } else if (!processing) {
        if (ctrl) {
          ctrl.stop()
          ctrl = null
        }
        // Final scan when streaming ends.
        scanAll()
      }
    },
  )

  // Scan when new messages arrive or tab switches.
  watch(
    () => {
      const tab = chat.activeTab
      if (!tab) return ""
      return tab + ":" + (chat.messagesByTab?.[tab]?.length || 0)
    },
    () => scanAll(),
  )

  onUnmounted(() => {
    if (ctrl) {
      ctrl.stop()
      ctrl = null
    }
  })
}
