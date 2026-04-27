<template>
  <div class="h-full min-h-0 flex flex-col gap-3 overflow-hidden">
    <!-- Filters (V3) + Live attach toggle (V4) -->
    <TraceFilters v-model="filters" :agents="agents" :live-status="liveStatus" />

    <!-- Timeline (V3) -->
    <TraceTimeline v-if="rollup.turns.length" @select-turn="onSelectTurn" />

    <!-- Live "↓ N new" banner (V4) -->
    <button v-if="filters.live && newSinceLastClear > 0 && !atBottom" class="self-end px-3 py-1 rounded-full bg-aquamarine/15 text-aquamarine text-[11px] font-mono shadow-md hover:bg-aquamarine/25" @click="scrollToBottom">{{ t("sessionViewer.trace.live.newBanner", { n: newSinceLastClear }) }}</button>

    <!-- Turn list -->
    <div ref="scrollEl" class="flex-1 min-h-0 overflow-y-auto flex flex-col gap-1.5" @scroll="onScroll">
      <div v-if="rollup.loading && !rollup.turns.length" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.trace.loading") }}</div>
      <div v-else-if="rollup.error" class="card p-4 text-coral text-sm">{{ rollup.error }}</div>
      <div v-else-if="!rollup.turns.length" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.trace.empty") }}</div>

      <TraceTurnGroup v-for="turn in displayedTurns" :key="`${rollup.agent}-${turn.turn_index}`" :turn="turn" :agent="rollup.agent" :session-name="detail.name" :expanded="expandedTurns.has(turn.turn_index)" :filters="filters" :live-events="liveEventsObjects" :selected-event-id="selectedEventId" @toggle="onToggle" @select-event="onSelectEvent" />
    </div>

    <!-- Event detail drawer -->
    <el-drawer v-model="detailOpen" :title="t('sessionViewer.detail.title')" direction="rtl" size="40%" :modal="false" :destroy-on-close="false">
      <TraceEventDetail :event="selectedEvent" @open-agent="onOpenSubagent" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import TraceEventDetail from "@/components/sessions/trace/TraceEventDetail.vue"
import TraceTab_TraceFilters from "@/components/sessions/trace/TraceFilters.vue"
import TraceTab_TraceTimeline from "@/components/sessions/trace/TraceTimeline.vue"
import TraceTab_TraceTurnGroup from "@/components/sessions/trace/TraceTurnGroup.vue"
import { useSessionEventStream } from "@/composables/useSessionEventStream"
import { useEventStreamStore } from "@/stores/eventStream"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useTurnRollupStore } from "@/stores/turnRollup"
import { useI18n } from "@/utils/i18n"

const TraceFilters = TraceTab_TraceFilters
const TraceTimeline = TraceTab_TraceTimeline
const TraceTurnGroup = TraceTab_TraceTurnGroup

const { t } = useI18n()
const detail = useSessionDetailStore()
const rollup = useTurnRollupStore()
const stream = useEventStreamStore()
const liveStream = useSessionEventStream()
const route = useRoute()
const router = useRouter()

const filters = ref({
  agent: "",
  errorsOnly: false,
  typeChips: [],
  live: false,
})

const expandedTurns = ref(new Set())
const scrollEl = ref(null)
const atBottom = ref(true)

// Event-detail panel state.
const selectedEvent = ref(null)
const detailOpen = ref(false)
const selectedEventId = computed(() => (selectedEvent.value && typeof selectedEvent.value.event_id === "number" ? selectedEvent.value.event_id : null))

function onSelectEvent(ev) {
  selectedEvent.value = ev
  detailOpen.value = true
}

function onOpenSubagent(namespace) {
  if (!namespace) return
  // Switch the agent filter — this re-fetches turns + events for the
  // sub-agent's namespace. Close the drawer so the user can see the
  // new trace; selectedEvent is preserved in case they reopen.
  filters.value = { ...filters.value, agent: namespace }
  detailOpen.value = false
}

const agents = computed(() => detail.agents || [])

const liveEvents = liveStream.events
const newSinceLastClear = liveStream.newSinceLastClear

const liveStatus = computed(() => {
  if (!filters.value.live) return ""
  if (liveStream.error.value) return liveStream.error.value
  if (liveStream.subscribed.value) return t("sessionViewer.trace.live.subscribed")
  return t("sessionViewer.trace.live.connecting")
})

// Convert {key, event} → event objects for the per-turn group rendering.
const liveEventsObjects = computed(() => liveEvents.value.map((e) => e.event))

// "Errors only" reflects in turn-level filter too: hide turns that have
// no error events. Turn rollups don't carry has_error today; we infer
// from the live events buffer + the rollup row presence in error_turns.
const displayedTurns = computed(() => {
  const turns = rollup.turns
  if (!filters.value.errorsOnly) return turns
  const errSet = new Set(detail.summary?.error_turns || [])
  return turns.filter((t2) => errSet.has(t2.turn_index))
})

function onToggle(turnIndex) {
  if (expandedTurns.value.has(turnIndex)) {
    expandedTurns.value.delete(turnIndex)
  } else {
    expandedTurns.value.add(turnIndex)
  }
  // Force reactive update — Set mutation isn't tracked.
  expandedTurns.value = new Set(expandedTurns.value)
}

function onSelectTurn(turnIndex) {
  expandedTurns.value = new Set([...expandedTurns.value, turnIndex])
  router.replace({ query: { ...route.query, turn: turnIndex } })
  nextTick(() => {
    const el = document.querySelector(`[data-turn="${turnIndex}"]`)
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" })
  })
}

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  atBottom.value = el.scrollTop + el.clientHeight >= el.scrollHeight - 16
  if (atBottom.value && newSinceLastClear.value > 0) {
    liveStream.clearNewCounter()
  }
}

function scrollToBottom() {
  const el = scrollEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  liveStream.clearNewCounter()
}

// Forward live events into the active turn's stream-store so
// expanded turn groups update in real time without a refetch.
watch(liveEvents, (arr) => {
  if (!arr.length) return
  const last = arr[arr.length - 1]
  if (last && last.event) stream.appendLive(last.event)
})

// Drive the rollup loader when name / agent changes.
watch(
  () => [detail.name, filters.value.agent],
  async ([name, agent]) => {
    if (!name) return
    const a = agent || agents.value[0] || null
    await rollup.load(name, a)
  },
  { immediate: true },
)

// Auto-pick first agent once meta loads.
watch(
  agents,
  (list) => {
    if (!filters.value.agent && list.length) {
      filters.value = { ...filters.value, agent: list[0] }
    }
  },
  { immediate: true },
)

// Live attach on/off.
watch(
  () => [filters.value.live, detail.name, filters.value.agent],
  ([live, name, agent]) => {
    if (live && name) {
      liveStream.attach(name, agent || null)
    } else {
      liveStream.detach()
    }
  },
)

// Deep-link: ``?turn=N`` opens that turn group.
watch(
  () => route.query.turn,
  (q) => {
    if (q == null) return
    const ti = Number(q)
    if (!Number.isFinite(ti)) return
    expandedTurns.value = new Set([...expandedTurns.value, ti])
  },
  { immediate: true },
)
</script>
