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
        <div class="kt-v2-edge ml-2 flex rounded-lg border overflow-hidden text-xs" role="radiogroup" :aria-label="t('lab.view.label')">
          <button v-for="m in VIEWS" :key="m.id" type="button" role="radio" :aria-checked="view === m.id" class="h-7 px-2.5 flex items-center gap-1.5" :class="view === m.id ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :data-test="`lab-view-${m.id}`" @click="setView(m.id)"><span :class="m.icon" />{{ t(`lab.view.${m.id}`) }}</button>
        </div>
        <span class="flex-1" />
        <button type="button" class="h-8 px-3 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow flex items-center gap-1.5" data-test="lab-new" @click="newOpen = true"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
      </template>
    </header>

    <div class="relative flex-1 min-h-0">
      <LabCanvas v-show="onBench" ref="canvasEl" :layout="layout" :structure="structure" :active-ids="activeIds" :last-messages="messages" @focus="focusedId = $event" @open="openSession" @menu="menu = $event" @new="newOpen = true" @resize="canvasWidth = $event" />
      <div v-if="onBench" class="absolute left-1/2 top-6 -translate-x-1/2 w-[min(40rem,calc(100%-2rem))] flex flex-col items-center gap-3 pointer-events-none">
        <p v-if="!tanks.length && !live.loading" class="m-0 text-[13px] text-warm-500 text-center" data-test="lab-empty">{{ t("lab.empty") }}</p>
        <LabRestoreBanner class="pointer-events-auto" @restored="live.refresh()" />
      </div>
      <Transition name="kt-lab-zoom">
        <div v-if="focused" class="absolute inset-0" data-test="lab-focus">
          <GraphSurface :key="focused.id" store-key="lab-focus" :session-id="focused.id" lock-session />
        </div>
        <div v-else-if="view === 'graph'" class="absolute inset-0" data-test="lab-graph">
          <GraphSurface store-key="lab-graph" />
        </div>
      </Transition>
      <LabHistoryBubble v-if="onBench" class="absolute left-4 bottom-4" :refresh-key="runningKey" :default-open="!tanks.length" />
    </div>
    <GraphContextMenu v-if="menuTank" :x="menu.x" :y="menu.y" :title="menuTank.name" :items="menuItems" @pick="onMenuPick" @close="menu = null" />

    <LabStatsStrip :tanks="tanks" />
    <NewSessionDialog v-if="newOpen" @close="newOpen = false" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import GraphContextMenu from "@/components/graph/canvas/GraphContextMenu.vue"
import { statusStyle } from "@/components/graph/graphTheme"
import GraphSurface from "@/components/graph/GraphSurface.vue"
import LabCanvas from "@/components/lab/LabCanvas.vue"
import LabHistoryBubble from "@/components/lab/LabHistoryBubble.vue"
import LabRestoreBanner from "@/components/lab/LabRestoreBanner.vue"
import LabStatsStrip from "@/components/lab/LabStatsStrip.vue"
import { activeTankIds, benchStructure, latestMessages } from "@/components/lab/model/labActivity"
import { buildTanks, layoutBench } from "@/components/lab/model/labLayout"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * The lab: every running session as a tank on one bench, or (Graph view) the
 * graph of every session. Click a tank to look inside it (its graph, in
 * place); Esc or ← comes back; right-click for its menu. Starting a session,
 * recent history, restart restores and the numbers sit around the bench.
 */
const GHOST = "kt-v2-edge kt-v2-panel h-8 px-2.5 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5"
const TICK_MS = 2000
const VIEW_KEY = "kt.lab.view"
const VIEWS = [
  { id: "bench", icon: "i-carbon-grid" },
  { id: "graph", icon: "i-carbon-network-3" },
]

const { t } = useI18n()
const tabs = useTabsStore()
const live = useGraphLiveStore()
const canvasEl = ref(null)
const canvasWidth = ref(1200)
const focusedId = ref(null)
const newOpen = ref(false)
const now = ref(Date.now())
const view = ref(readView())
const menu = ref(null)
let ticker = null

function readView() {
  try {
    return localStorage.getItem(VIEW_KEY) === "graph" ? "graph" : "bench"
  } catch {
    return "bench"
  }
}

function setView(next) {
  view.value = next
  focusedId.value = null
  try {
    localStorage.setItem(VIEW_KEY, next)
  } catch {
    /* the view just isn't remembered */
  }
}

const tanks = computed(() => buildTanks(live.model))
const layout = computed(() => layoutBench(tanks.value, { width: canvasWidth.value }))
const structure = computed(() => benchStructure(tanks.value))
const runningKey = computed(() => tanks.value.map((tk) => tk.id).join(","))
const activeIds = computed(() => activeTankIds(tanks.value, live.pulses, now.value))
const messages = computed(() => latestMessages(tanks.value, live.lastMessages))
const focused = computed(() => tanks.value.find((tk) => tk.id === focusedId.value) || null)
const onBench = computed(() => !focused.value && view.value === "bench")
const menuTank = computed(() => (menu.value ? tanks.value.find((tk) => tk.id === menu.value.id) || null : null))
const menuItems = computed(() => [
  { id: "inside", label: t("lab.menu.inside"), icon: "i-carbon-zoom-in" },
  { id: "open", label: t("lab.tank.open"), icon: "i-carbon-launch" },
  { id: "inspector", label: t("lab.menu.inspector"), icon: "i-carbon-radar" },
  { id: "d1", divider: true },
  { id: "stop", label: t("graph.action.stopSession"), icon: "i-carbon-power", danger: true },
])

function openSession(id) {
  const tank = tanks.value.find((tk) => tk.id === id)
  tabs.openSurface(id, "chat", tank ? { config_name: tank.name } : {})
}

async function stopSession(tank) {
  try {
    await ElMessageBox.confirm(t("graph.confirm.stopSessionBody", { n: tank.size }), t("graph.confirm.stopSession", { name: tank.name }), {
      type: "warning",
      confirmButtonText: t("graph.action.stopSession"),
      cancelButtonText: t("graph.action.cancel"),
    })
  } catch {
    return
  }
  try {
    await sessionAPI.stopActive(tank.id)
    await live.refresh()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  }
}

function onMenuPick(id) {
  const tank = menuTank.value
  menu.value = null
  if (!tank) return
  if (id === "inside") focusedId.value = tank.id
  else if (id === "open") openSession(tank.id)
  else if (id === "inspector") tabs.openSurface(tank.id, "inspector", { config_name: tank.name })
  else if (id === "stop") stopSession(tank)
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
