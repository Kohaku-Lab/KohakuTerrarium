/**
 * useArtifactDetector — watches the chat store for assistant messages
 * and scans them for canvas artifacts. Runs globally (App.vue).
 *
 * Scans periodically while processing (every 2s) to catch completed
 * code blocks mid-stream, plus a final scan when processing ends.
 */

import { onUnmounted, watch } from "vue"

import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"

export function useArtifactDetector() {
  const chat = useChatStore()
  const canvas = useCanvasStore()
  let intervalId = null

  function scanAll() {
    const tab = chat.activeTab
    if (!tab) return
    const msgs = chat.messagesByTab?.[tab] || []
    for (const m of msgs) {
      canvas.scanMessage(m)
    }
  }

  // While processing, scan every 2s to catch completed code blocks mid-stream.
  watch(
    () => chat.processing,
    (processing) => {
      if (processing && !intervalId) {
        intervalId = setInterval(scanAll, 2000)
      } else if (!processing) {
        if (intervalId) {
          clearInterval(intervalId)
          intervalId = null
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
    if (intervalId) {
      clearInterval(intervalId)
      intervalId = null
    }
  })
}
