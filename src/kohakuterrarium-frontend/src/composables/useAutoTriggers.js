/**
 * useAutoTriggers — subscribes to the chat store's running state and
 * fires notifications / preset switches on specific events.
 *
 * Phase 10 scope: ship a minimal rule set. The Settings Auto-open
 * tab can later override actions per rule.
 *
 * Current rules:
 *   - processing_error      → notify + auto-focus debug preset
 *   - first canvas artifact → notify (no focus steal)
 *   - job promoted to bg    → notify
 */

import { onMounted, onUnmounted, watch } from "vue"

import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"
import { useLayoutStore } from "@/stores/layout"
import { useNotificationsStore } from "@/stores/notifications"

export function useAutoTriggers() {
  const chat = useChatStore()
  const canvas = useCanvasStore()
  const layout = useLayoutStore()
  const notifications = useNotificationsStore()

  let stopCanvasWatch = null
  let stopErrorWatch = null

  onMounted(() => {
    // Rule: first canvas artifact of the session → notify (no steal).
    stopCanvasWatch = watch(
      () => canvas.artifacts.length,
      (len, prev) => {
        if (len > (prev ?? 0) && (prev ?? 0) === 0) {
          notifications.push({
            level: "info",
            title: "Canvas artifact ready",
            body: "Switch to the Canvas preset (Ctrl+4) to view it.",
          })
        }
      },
    )

    // Rule: processing error events (drip from the chat store) →
    // steal focus to the debug preset. This is the one rule that is
    // allowed to steal focus per the design doc.
    stopErrorWatch = watch(
      () => {
        const tab = chat.activeTab
        if (!tab) return 0
        const msgs = chat.messagesByTab?.[tab] || []
        const last = msgs[msgs.length - 1]
        // Heuristic: a compact error shows up as a compact msg with
        // status !== 'done' and a summary that mentions "error".
        if (last && last.role === "system" && /error/i.test(last.content || "")) {
          return msgs.length
        }
        return 0
      },
      (len, prev) => {
        if (len > (prev ?? 0)) {
          notifications.push({
            level: "error",
            title: "Agent error",
            body: "Opening the debug panel.",
          })
          layout.switchPreset("debug")
        }
      },
    )
  })

  onUnmounted(() => {
    if (stopCanvasWatch) stopCanvasWatch()
    if (stopErrorWatch) stopErrorWatch()
  })
}
