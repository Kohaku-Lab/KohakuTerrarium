<template>
  <div class="kt-v2 kt-v2-canvas h-full flex flex-col overflow-hidden" data-test="lab">
    <header class="kt-v2-line shrink-0 h-[52px] flex items-center gap-3 px-5 border-b">
      <h1 class="text-[15px] font-semibold text-warm-800 dark:text-warm-100">{{ t("lab.title") }}</h1>
      <div class="flex items-center gap-3 text-xs text-warm-500" data-test="lab-counts">
        <template v-if="totals.running">
          <span data-test="lab-count-running"
            ><span class="font-mono text-warm-700 dark:text-warm-200">{{ totals.running }}</span> {{ t("lab.count.runningWord") }}</span
          >
          <span data-test="lab-count-creatures"
            ><span class="font-mono text-warm-700 dark:text-warm-200">{{ totals.creatures }}</span> {{ t("lab.count.creaturesWord", { n: totals.creatures }) }}</span
          >
          <span v-if="totals.busy" data-test="lab-count-busy"
            ><span class="font-mono text-aquamarine-shadow dark:text-aquamarine-light">{{ totals.busy }}</span> {{ t("lab.count.workingWord") }}</span
          >
          <span v-if="totals.machines > 1" data-test="lab-count-machines"
            ><span class="font-mono text-warm-700 dark:text-warm-200">{{ totals.machines }}</span> {{ t("lab.count.machinesWord", { n: totals.machines }) }}</span
          >
        </template>
        <span v-else-if="!loading">{{ t("lab.nothingRunning") }}</span>
      </div>
      <span class="flex-1" />
      <button type="button" class="h-8 px-2.5 rounded-lg border text-xs flex items-center gap-1.5" :class="view === 'graph' ? 'border-iolite/50 bg-iolite/10 text-iolite dark:text-iolite-light' : 'kt-v2-edge kt-v2-panel text-warm-700 dark:text-warm-200 hover:border-iolite/50'" :aria-pressed="view === 'graph'" :title="t('lab.graphAll.hint')" data-test="lab-graph-all" @click="toggleGraph"><span class="i-carbon-network-3" />{{ t("lab.graphAll") }}</button>
      <button type="button" class="h-8 px-3 rounded-lg text-xs font-medium bg-iolite text-white hover:bg-iolite-shadow flex items-center gap-1.5" data-test="lab-new" @click="$emit('new', null)"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
    </header>

    <div class="relative flex-1 min-h-0 flex">
      <section class="flex-1 min-w-0 min-h-0 flex flex-col gap-2.5 px-5 pt-3.5 pb-4 overflow-hidden">
        <LabRestoreBanner @restored="live.refresh()" />
        <template v-if="sessions.length">
          <h2 class="shrink-0 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
            {{ t("lab.running") }}<span class="font-mono font-normal tracking-normal text-warm-400">{{ sessions.length }}</span>
          </h2>
          <div ref="benchEl" class="flex-1 min-h-0 overflow-y-auto" data-test="lab-bench">
            <div class="grid content-start items-start" :style="{ gap: `${TILE.gap}px`, gridTemplateColumns: `repeat(auto-fill, minmax(${TILE.minWidth}px, 1fr))` }">
              <LabSessionTile v-for="s in sessions" :key="s.id" :session="s" :active="active.has(s.id)" :digest="digests[s.id] || null" :level="level" @focus="focusedId = $event" @open="actions.openChat" @menu="menu = $event" />
            </div>
          </div>
        </template>
        <div v-else-if="!loading" class="flex-1 min-h-0 flex items-center justify-center">
          <LabEmptyHero :starts="recent.starts.value" @new="$emit('new', $event)" />
        </div>
      </section>
      <LabRecentPanel :recent="recent" />

      <Transition name="kt-lab-zoom">
        <LabFocus v-if="focused" :session="focused" :sessions="sessions" class="z-10" @back="focusedId = null" @focus="focusedId = $event" @open="actions.openChat" @inspector="actions.openInspector" />
        <div v-else-if="view === 'graph'" class="kt-v2-canvas absolute inset-0 z-10" data-test="lab-graph">
          <GraphSurface store-key="lab-graph" />
        </div>
      </Transition>
    </div>

    <GraphContextMenu v-if="menuSession" :x="menu.x" :y="menu.y" :title="menuSession.name" :items="menuItems" @pick="onMenuPick" @close="menu = null" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import GraphContextMenu from "@/components/graph/canvas/GraphContextMenu.vue"
import GraphSurface from "@/components/graph/GraphSurface.vue"
import LabRestoreBanner from "@/components/lab/LabRestoreBanner.vue"
import LabFocus from "@/components/lab/desktop/LabFocus.vue"
import LabRecentPanel from "@/components/lab/desktop/LabRecentPanel.vue"
import LabSessionTile from "@/components/lab/desktop/LabSessionTile.vue"
import { TILE, tileLevel } from "@/components/lab/model/labSessions"
import LabEmptyHero from "@/components/lab/shared/LabEmptyHero.vue"
import { useI18n } from "@/utils/i18n"

/**
 * The lab on a desktop, one screen with no page scroll: counts, Graph of
 * everything and New session on top; running sessions as tiles, at the
 * richest level that fits the region, beside the Recent panel. A tile opens
 * its session's live graph in place; Esc or ← comes back.
 */
const props = defineProps({
  bench: { type: Object, required: true },
  recent: { type: Object, required: true },
  actions: { type: Object, required: true },
})
defineEmits(["new"])

const VIEW_KEY = "kt.lab.view"
const { t } = useI18n()
const live = props.bench.live
const sessions = computed(() => props.bench.sessions.value)
const active = computed(() => props.bench.active.value)
const digests = computed(() => props.bench.digests.value)
const totals = computed(() => props.bench.totals.value)
const loading = computed(() => props.bench.loading.value)

const benchEl = ref(null)
const benchSize = ref({ w: 0, h: 0 })
const focusedId = ref(null)
const menu = ref(null)
const view = ref(readView())
let observer = null

const level = computed(() => tileLevel(sessions.value.length, benchSize.value.w, benchSize.value.h))
const focused = computed(() => sessions.value.find((s) => s.id === focusedId.value) || null)
const menuSession = computed(() => (menu.value ? sessions.value.find((s) => s.id === menu.value.id) || null : null))
const menuItems = computed(() => [
  { id: "inside", label: t("lab.menu.inside"), icon: "i-carbon-zoom-in" },
  { id: "open", label: t("lab.session.open"), icon: "i-carbon-chat" },
  { id: "inspector", label: t("lab.menu.inspector"), icon: "i-carbon-radar" },
  { id: "d1", divider: true },
  { id: "stop", label: t("graph.action.stopSession"), icon: "i-carbon-power", danger: true },
])

function readView() {
  try {
    return localStorage.getItem(VIEW_KEY) === "graph" ? "graph" : "bench"
  } catch {
    return "bench"
  }
}

function toggleGraph() {
  view.value = view.value === "graph" && !focusedId.value ? "bench" : "graph"
  focusedId.value = null
  try {
    localStorage.setItem(VIEW_KEY, view.value)
  } catch {
    /* the view just isn't remembered */
  }
}

function onMenuPick(id) {
  const session = menuSession.value
  menu.value = null
  if (!session) return
  if (id === "inside") focusedId.value = session.id
  else if (id === "open") props.actions.openChat(session)
  else if (id === "inspector") props.actions.openInspector(session)
  else if (id === "stop") props.actions.stop(session)
}

// A session that ends while looked at returns to the bench.
watch(focused, (session) => {
  if (focusedId.value && !session) focusedId.value = null
})

function observe(el) {
  observer?.disconnect()
  if (!el || typeof ResizeObserver === "undefined") return
  observer = new ResizeObserver(([entry]) => {
    benchSize.value = { w: entry.contentRect.width, h: entry.contentRect.height }
  })
  observer.observe(el)
}
watch(benchEl, observe)

// Esc leaves a looked-at session unless a field, dialog or the graph already took it.
function onKeydown(e) {
  if (e.key !== "Escape" || !focusedId.value || e.defaultPrevented) return
  if (e.target?.closest?.("input, textarea, select, [contenteditable='true'], [role='dialog']")) return
  focusedId.value = null
}

onMounted(() => window.addEventListener("keydown", onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown)
  observer?.disconnect()
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
  transform: scale(0.98);
}
</style>
