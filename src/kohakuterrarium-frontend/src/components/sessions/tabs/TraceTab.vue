<template>
  <div class="h-full min-h-0 flex flex-col gap-3 overflow-hidden">
    <!-- Filters (V3) + Live attach toggle (V4) -->
    <TraceFilters v-model="filters" :agents="agents" :live-status="liveStatus" />

    <!-- Timeline (V3): lane overview with drag-to-focus interval -->
    <TraceTimeline v-if="timelineModel" :model="timelineModel" v-model:mode="timelineMode" v-model:range="timelineRange" :truncated="timeline.truncated" @select-span="onSelectSpan" />

    <!-- Focus chip + fold-all controls -->
    <div v-if="timelineRange || rollup.turns.length" class="flex items-center gap-2 text-[11px]">
      <span v-if="timelineRange" class="px-2 py-0.5 rounded-full bg-iolite/15 text-iolite font-mono flex items-center gap-1.5">
        {{ t("sessionViewer.trace.timeline.focusTurns", { n: displayedTurns.length }) }}
        <button class="i-carbon-close hover:text-coral" :title="t('sessionViewer.trace.timeline.clearFocus')" @click="timelineRange = null" />
      </span>
      <span class="flex-1" />
      <button class="text-warm-500 hover:text-warm-700 dark:hover:text-warm-300" @click="expandAll">{{ t("sessionViewer.trace.turn.expandAll") }}</button>
      <button class="text-warm-500 hover:text-warm-700 dark:hover:text-warm-300" @click="collapseAll">{{ t("sessionViewer.trace.turn.collapseAll") }}</button>
    </div>

    <!-- Live "↓ N new" banner (V4) -->
    <button v-if="filters.live && newSinceLastClear > 0 && !atBottom" class="self-end px-3 py-1 rounded-full bg-aquamarine/15 text-aquamarine text-[11px] font-mono shadow-md hover:bg-aquamarine/25" @click="scrollToBottom">{{ t("sessionViewer.trace.live.newBanner", { n: newSinceLastClear }) }}</button>

    <!-- Turn list (virtualized) -->
    <div ref="scrollEl" class="flex-1 min-h-0 overflow-y-auto" @scroll="onScroll">
      <div v-if="rollup.loading && !rollup.turns.length" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.trace.loading") }}</div>
      <div v-else-if="rollup.error" class="card p-4 text-coral text-sm">{{ rollup.error }}</div>
      <div v-else-if="!rollup.turns.length" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.trace.empty") }}</div>

      <div v-else class="relative" :style="{ height: `${totalSize}px` }">
        <div v-for="vRow in virtualRows" :key="vRow.key" :data-index="vRow.index" :ref="measureRow" class="absolute top-0 left-0 w-full pb-1.5" :style="{ transform: `translateY(${vRow.start}px)` }">
          <TraceTurnGroup :turn="displayedTurns[vRow.index]" :agent="rollup.agent" :session-name="detail.name" :expanded="expandedTurns.has(displayedTurns[vRow.index].turn_index)" :filters="filters" :live-events="liveEventsObjects" :selected-event-id="selectedEventId" :focus-event-ids="focusEventIds" :jump-event-id="jumpIdFor(displayedTurns[vRow.index].turn_index)" :jump-member="jumpMemberFor(displayedTurns[vRow.index].turn_index)" @toggle="onToggle" @select-event="onSelectEvent" @matches="onTurnMatches" @jumped="onJumped" />
        </div>
      </div>
    </div>

    <!-- Event detail drawer -->
    <el-drawer v-model="detailOpen" :title="t('sessionViewer.detail.title')" direction="rtl" size="40%" :modal="false" :destroy-on-close="false">
      <TraceEventDetail :event="selectedEvent" @open-agent="onOpenSubagent" />
    </el-drawer>
  </div>
</template>

<script setup>
import { useVirtualizer } from "@tanstack/vue-virtual"
import { computed, nextTick, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import TraceEventDetail from "@/components/sessions/trace/TraceEventDetail.vue"
import TraceTab_TraceFilters from "@/components/sessions/trace/TraceFilters.vue"
import TraceTab_TraceTimeline from "@/components/sessions/trace/TraceTimeline.vue"
import TraceTab_TraceTurnGroup from "@/components/sessions/trace/TraceTurnGroup.vue"
import { parseSearchTerms } from "@/components/sessions/trace/traceSearch"
import { deriveTraceTimeline, traceTimelineFocus } from "@/components/sessions/trace/traceTimeline"
import { useSessionEventStream } from "@/composables/useSessionEventStream"
import { useEventStreamStore } from "@/stores/eventStream"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useTraceTimelineStore } from "@/stores/traceTimeline"
import { useTurnRollupStore } from "@/stores/turnRollup"
import { useI18n } from "@/utils/i18n"

const TraceFilters = TraceTab_TraceFilters
const TraceTimeline = TraceTab_TraceTimeline
const TraceTurnGroup = TraceTab_TraceTurnGroup

const { t } = useI18n()
const detail = useSessionDetailStore()
const rollup = useTurnRollupStore()
const timeline = useTraceTimelineStore()
const stream = useEventStreamStore()
const liveStream = useSessionEventStream()
const route = useRoute()
const router = useRouter()

const filters = ref({
  agent: "",
  errorsOnly: false,
  typeChips: [],
  live: false,
  search: "",
})

const expandedTurns = ref(new Set())
const scrollEl = ref(null)
const atBottom = ref(true)

// Lane-timeline focus: projection mode + selected interval in its domain.
const timelineMode = ref("sequence")
const timelineRange = ref(null)

const timelineModel = computed(() => deriveTraceTimeline(timeline.records, timelineMode.value))

const timelineFocus = computed(() => {
  if (!timelineRange.value || !timelineModel.value) return null
  return traceTimelineFocus(timelineModel.value, timelineRange.value)
})

const focusEventIds = computed(() => timelineFocus.value?.eventIds ?? null)

// Event search (Phase 3): auto-expand turns while a query is active so
// their events load and can be filtered; groups report match counts.
// Declared before ``displayedTurns`` — the virtualizer evaluates it
// eagerly during setup.
const searchActive = computed(() => parseSearchTerms(filters.value.search).length > 0)
const zeroMatchTurns = ref(new Set())

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
  let turns = rollup.turns
  if (filters.value.errorsOnly) {
    const errSet = new Set(detail.summary?.error_turns || [])
    turns = turns.filter((t2) => errSet.has(t2.turn_index))
  }
  const focus = timelineFocus.value
  if (focus) turns = turns.filter((t2) => focus.turns.has(t2.turn_index))
  // While searching, turns whose loaded events have zero matches are
  // hidden (reported by the turn groups via ``matches``).
  if (searchActive.value && zeroMatchTurns.value.size) {
    turns = turns.filter((t2) => !zeroMatchTurns.value.has(t2.turn_index))
  }
  return turns
})

// Virtualized turn list: one row per turn, dynamically measured so
// expanded groups can grow arbitrarily tall.
const rowVirtualizer = useVirtualizer(
  computed(() => ({
    count: displayedTurns.value.length,
    getScrollElement: () => scrollEl.value,
    estimateSize: () => 46,
    getItemKey: (i) => `${rollup.agent}-${displayedTurns.value[i]?.turn_index ?? i}`,
    overscan: 10,
  })),
)
const virtualRows = computed(() => rowVirtualizer.value.getVirtualItems())
const totalSize = computed(() => rowVirtualizer.value.getTotalSize())
const measureRow = (el) => rowVirtualizer.value.measureElement(el)

function onTurnMatches(turnIndex, count) {
  const next = new Set(zeroMatchTurns.value)
  if (count === 0) next.add(turnIndex)
  else next.delete(turnIndex)
  zeroMatchTurns.value = next
}

watch(
  () => filters.value.search,
  (q) => {
    // Re-querying must un-hide turns so their groups remount + re-report.
    zeroMatchTurns.value = new Set()
    if (parseSearchTerms(q).length) expandAll()
  },
)

function expandAll() {
  expandedTurns.value = new Set(displayedTurns.value.map((t2) => t2.turn_index))
}

function collapseAll() {
  expandedTurns.value = new Set()
}

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
  // Selecting a turn outside the focused interval clears the focus, like
  // the reference trajectory UI — otherwise the target would stay hidden.
  if (timelineFocus.value && !timelineFocus.value.turns.has(turnIndex)) {
    timelineRange.value = null
  }
  expandedTurns.value = new Set([...expandedTurns.value, turnIndex])
  router.replace({ query: { ...route.query, turn: turnIndex } })
  nextTick(() => {
    const index = displayedTurns.value.findIndex((t2) => t2.turn_index === turnIndex)
    if (index >= 0) rowVirtualizer.value.scrollToIndex(index, { align: "center" })
  })
}

// Timeline span click: expand the span's turn, then hand the event id to
// that turn's group ONLY — it scrolls the exact event row into view once
// loaded (auto-paging through the turn's event pages when needed). The
// target must not reach other groups: they would exhaust immediately and
// clear it before the real group finishes loading.
const jumpTarget = ref(null)

function jumpIdFor(turnIndex) {
  const t2 = jumpTarget.value
  return t2 && t2.turn === turnIndex ? t2.eid : null
}

function jumpMemberFor(turnIndex) {
  const t2 = jumpTarget.value
  return t2 && t2.turn === turnIndex ? t2.member : null
}

function onSelectSpan(span) {
  if (!span || span.turn == null) return
  jumpTarget.value = typeof span.index === "number" ? { turn: span.turn, eid: span.index, member: span.member ?? null } : null
  onSelectTurn(span.turn)
}

function onJumped({ event }) {
  jumpTarget.value = null
  if (event) selectedEvent.value = event
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
  if (last && last.event) {
    stream.appendLive(last.event)
    timeline.appendLive(last.event)
  }
})

// Drive the rollup loader when name / agent changes. ``detail.reloadKey``
// re-runs it so a live session's new turns appear without a refresh (UXI-01).
watch(
  () => [detail.name, filters.value.agent, detail.reloadKey],
  async ([name, agent]) => {
    if (!name) return
    const a = agent || agents.value[0] || null
    timelineRange.value = null
    zeroMatchTurns.value = new Set()
    await Promise.all([rollup.load(name, a), timeline.load(name, a)])
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
