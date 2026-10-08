<template>
  <div class="kt-v2 kt-v2-canvas h-full flex flex-col overflow-hidden" data-test="lab">
    <header class="kt-v2-line border-b shrink-0 h-12 flex items-center gap-2 px-4">
      <template v-if="focused">
        <button type="button" class="h-8 px-2 rounded-lg flex items-center gap-1.5 text-[13px] text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" data-test="lab-back" @click="focusedId = null"><span class="i-carbon-arrow-left" />{{ t("lab.title") }}</button>
        <span class="text-warm-400">/</span>
        <span class="w-2 h-2 rounded-full shrink-0" :class="statusStyle(focused.status).dot" />
        <h1 class="text-[14px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ focused.name }}</h1>
        <span class="flex-1" />
        <button type="button" :class="GHOST" data-test="lab-focus-open" @click="openSession(focused.id)"><span class="i-carbon-launch" />{{ t("lab.tank.open") }}</button>
      </template>
      <template v-else>
        <h1 class="text-[15px] font-semibold text-warm-800 dark:text-warm-100">{{ t("lab.title") }}</h1>
        <span class="text-[12px] text-warm-500">{{ t("lab.summary", { n: tanks.length }) }}</span>
        <span class="flex-1" />
        <button type="button" :class="GHOST" data-test="lab-open-graph" @click="tabs.openTab({ kind: 'graph', id: 'graph' })"><span class="i-carbon-network-3" />{{ t("lab.openGraph") }}</button>
        <button type="button" class="h-8 px-3 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow flex items-center gap-1.5" data-test="lab-new" @click="newOpen = true"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
      </template>
    </header>

    <div class="relative flex-1 min-h-0">
      <LabCanvas v-show="!focused" ref="canvasEl" :layout="layout" :structure="structure" :active-ids="activeIds" :last-messages="messages" @focus="focusedId = $event" @open="openSession" @new="newOpen = true" @resize="canvasWidth = $event" />
      <p v-if="!focused && !tanks.length && !live.loading" class="absolute left-1/2 top-6 -translate-x-1/2 text-[13px] text-warm-500 text-center pointer-events-none" data-test="lab-empty">{{ t("lab.empty") }}</p>
      <Transition name="kt-lab-zoom">
        <div v-if="focused" class="absolute inset-0" data-test="lab-focus">
          <GraphSurface :key="focused.id" store-key="lab-focus" :session-id="focused.id" lock-session />
        </div>
      </Transition>
      <LabHistoryBubble v-if="!focused" class="absolute left-4 bottom-4" :refresh-key="runningKey" :default-open="!tanks.length" />
    </div>

    <LabStatsStrip :tanks="tanks" />
    <NewSessionDialog v-if="newOpen" @close="newOpen = false" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { statusStyle } from "@/components/graph/graphTheme"
import GraphSurface from "@/components/graph/GraphSurface.vue"
import LabCanvas from "@/components/lab/LabCanvas.vue"
import LabHistoryBubble from "@/components/lab/LabHistoryBubble.vue"
import LabStatsStrip from "@/components/lab/LabStatsStrip.vue"
import { activeTankIds, benchStructure, latestMessages } from "@/components/lab/model/labActivity"
import { buildTanks, layoutBench } from "@/components/lab/model/labLayout"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/**
 * The lab: every running session as a tank on one bench. Click a tank to
 * look inside it (its graph, in place); Esc or ← comes back. Starting a
 * session, recent history and the numbers sit around the bench.
 */
const GHOST = "kt-v2-edge kt-v2-panel h-8 px-2.5 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5"
const TICK_MS = 2000

const { t } = useI18n()
const tabs = useTabsStore()
const live = useGraphLiveStore()
const canvasEl = ref(null)
const canvasWidth = ref(1200)
const focusedId = ref(null)
const newOpen = ref(false)
const now = ref(Date.now())
let ticker = null

const tanks = computed(() => buildTanks(live.model))
const layout = computed(() => layoutBench(tanks.value, { width: canvasWidth.value }))
const structure = computed(() => benchStructure(tanks.value))
const runningKey = computed(() => tanks.value.map((tk) => tk.id).join(","))
const activeIds = computed(() => activeTankIds(tanks.value, live.pulses, now.value))
const messages = computed(() => latestMessages(tanks.value, live.lastMessages))
const focused = computed(() => tanks.value.find((tk) => tk.id === focusedId.value) || null)

function openSession(id) {
  const tank = tanks.value.find((tk) => tk.id === id)
  tabs.openSurface(id, "chat", tank ? { config_name: tank.name } : {})
}

// A session that ends while looked at returns to the bench.
watch(focused, (tank) => {
  if (focusedId.value && !tank) focusedId.value = null
})

// Esc leaves a looked-at session unless a field, dialog or the graph already took it.
function onKeydown(e) {
  if (e.key !== "Escape" || !focusedId.value || e.defaultPrevented) return
  if (e.target?.closest?.("input, textarea, select, [contenteditable='true'], [role='dialog']")) return
  focusedId.value = null
}

onMounted(() => {
  live.acquire()
  ticker = createVisibilityInterval(() => (now.value = Date.now()), TICK_MS)
  ticker.start()
  window.addEventListener("keydown", onKeydown)
})
onBeforeUnmount(() => {
  live.release()
  ticker?.stop()
  window.removeEventListener("keydown", onKeydown)
})
</script>

<style scoped>
.kt-lab-zoom-enter-active,
.kt-lab-zoom-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}
.kt-lab-zoom-enter-from,
.kt-lab-zoom-leave-to {
  opacity: 0;
  transform: scale(0.96);
}
</style>
