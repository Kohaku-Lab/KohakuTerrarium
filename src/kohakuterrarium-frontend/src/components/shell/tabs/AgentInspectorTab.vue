<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Compact live-status header — folds the live-only data (running
         jobs, live token counts, scratchpad, ctx%) onto the viewer frame. -->
    <InspectorHeader :target="target" :instance="instance" :session-name="sessionName" />

    <!-- The Inspect surface IS the session-history-viewer, bound to the
         LIVE session id: same Overview / Trace / Conversation / Cost tabs
         a saved session gets, for the running session (UXI-01). -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <SessionViewerPage v-if="sessionName" :key="sessionName" :session-name-prop="sessionName" :live="true" />
      <div v-else class="p-4 text-warm-400 italic text-sm">No active session.</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from "vue"

import InspectorHeader from "@/components/shell/tabs/InspectorHeader.vue"
import SessionViewerPage from "@/components/sessions/pages/SessionViewerPage.vue"
import { provideScope } from "@/composables/useScope"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useSessionDetailStore } from "@/stores/sessionDetail"

const props = defineProps({ tab: { type: Object, required: true } })

const target = computed(() => props.tab.target)
const instances = useInstancesStore()
const instance = computed(() => instances.list.find((i) => i.id === target.value) ?? null)

// Scope so the header + embedded viewer resolve this target's stores.
provideScope(target.value)

// Open the live feed so the header's jobs / tokens / scratchpad update.
// Pass the scope explicitly (Vue's inject doesn't see a same-component
// provide). initForInstance short-circuits when already bound, so an
// inspector alongside a chat tab reuses the live WS.
const chat = useChatStore(target.value)

// An active session already persists a store the history-viewer reads by
// name; ``instance.id`` (= session_id) IS that name.
const sessionName = computed(() => instance.value?.session_id || target.value)

// Same scoped session-detail store the embedded SessionViewerPage uses —
// bumping ``requestReload`` on it drives every viewer tab to refetch.
const detail = useSessionDetailStore(sessionName.value)

// The embedded viewer fetches once; drive it to refetch as the LIVE
// session advances. ``SessionViewerPage`` is display-only history — the
// WS feed lands in the chat store, so watch a coarse "a round changed"
// signal (top-level message count + processing edge; text chunks don't
// bump the count), debounce a burst into one refetch, and skip while the
// browser tab is hidden. This inspector is mounted only while its macro
// tab is the visible one (TabContent uses v-if), so no extra tab gating.
const roundSignal = computed(() => {
  let count = 0
  for (const arr of Object.values(chat.messagesByTab || {})) count += arr?.length || 0
  return `${count}|${chat.anyProcessing}`
})

let _reloadTimer = null
function scheduleReload() {
  if (_reloadTimer) clearTimeout(_reloadTimer)
  _reloadTimer = setTimeout(() => {
    _reloadTimer = null
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return
    detail.requestReload()
  }, 1200)
}
watch(roundSignal, scheduleReload)
onUnmounted(() => {
  if (_reloadTimer) clearTimeout(_reloadTimer)
})

async function ensureFeed() {
  let inst = instance.value
  if (!inst && typeof instances.fetchOne === "function") {
    try {
      inst = await instances.fetchOne(target.value)
    } catch {
      /* swallow — instance may not exist; UI degrades gracefully */
    }
  }
  if (inst) {
    chat.initForInstance(inst)
  }
}
onMounted(ensureFeed)
watch(target, ensureFeed)
</script>
