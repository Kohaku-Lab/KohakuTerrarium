/**
 * useArtifactDetector — watches the chat store for assistant messages
 * and scans them for canvas artifacts.
 *
 * Scans periodically while processing (every 2s) to catch completed
 * code blocks mid-stream, plus a final scan when processing ends.
 * Also scans immediately when the owning macro tab/panel is activated
 * so artifacts produced while the user was elsewhere are caught up.
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

import { onMounted, onUnmounted, watch } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"

export function useArtifactDetector(scope, options = {}) {
  const { active = null } = options
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

  // Scan when new messages arrive or inner chat tab switches.
  watch(
    () => {
      const tab = chat.activeTab
      if (!tab) return ""
      const msgs = chat.messagesByTab?.[tab] || []
      const last = msgs[msgs.length - 1]
      const lastParts = Array.isArray(last?.parts) ? last.parts.length : 0
      return [tab, msgs.length, last?.id || "", lastParts, chat.processing].join(":")
    },
    () => scanAll(),
  )

  // Catch up when the owning macro tab/panel becomes active again.
  // This covers the common path where a retry finishes while the user
  // is looking at another session or another workspace preset; on
  // return, the chat has fresh messages but the interval/final scan
  // might have been skipped because this scope was inactive.
  if (active && typeof active === "object" && "value" in active) {
    watch(
      () => active.value,
      (isActive) => {
        if (isActive) scanAll()
      },
    )
  }

  onMounted(scanAll)

  onUnmounted(() => {
    if (ctrl) {
      ctrl.stop()
      ctrl = null
    }
  })
}
