import { onBeforeUnmount, onMounted, watch } from "vue"

import { subscribeAttention, totalAttention } from "@/stores/attention"
import { useAttentionPrefs } from "@/stores/attentionPrefs"

const BASE_TITLE = "KohakuTerrarium"

export function attentionDocumentTitle(summary) {
  if (summary.pending > 0) return `(!) ${BASE_TITLE}`
  if (summary.completed > 0) return `(${summary.completed}) ${BASE_TITLE}`
  return BASE_TITLE
}

export function useDocumentAttention() {
  const prefs = useAttentionPrefs()
  let unsubscribe

  function updateTitle() {
    if (typeof document === "undefined") return
    document.title = prefs.state.dynamicTitle
      ? attentionDocumentTitle(totalAttention())
      : BASE_TITLE
  }

  onMounted(() => {
    unsubscribe = subscribeAttention(updateTitle)
    updateTitle()
  })
  onBeforeUnmount(() => unsubscribe?.())
  watch(() => prefs.state.dynamicTitle, updateTitle)
}
