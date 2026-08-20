<template>
  <div ref="rootEl" class="card p-2 flex flex-col gap-1">
    <!-- Header row -->
    <button class="w-full flex items-center gap-2 text-[12px] text-left py-1 px-1 rounded hover:bg-warm-50 dark:hover:bg-warm-800/50" @click="onToggle">
      <span class="i-carbon-chevron-right transition-transform shrink-0" :class="expanded ? 'rotate-90 text-iolite' : 'text-warm-400'" />
      <span class="font-mono w-16 shrink-0 text-warm-700 dark:text-warm-300">#{{ turn.turn_index }}</span>
      <span v-if="agent" class="font-mono text-warm-400 w-20 shrink-0 truncate">{{ agent }}</span>
      <span v-if="durationS != null" class="text-warm-500 shrink-0">{{ t("sessionViewer.trace.turn.duration", { s: durationS }) }}</span>
      <span v-if="tokenStr" class="font-mono text-warm-500 shrink-0">{{ tokenStr }}</span>
      <span v-if="costStr" class="text-warm-700 dark:text-warm-300 shrink-0">{{ costStr }}</span>
      <span v-if="subagentCount" class="px-1 py-0 rounded bg-iolite/10 text-iolite text-[10px] font-mono shrink-0">sub×{{ subagentCount }}</span>
      <span v-if="toolCount" class="text-warm-500 shrink-0">{{ t("sessionViewer.trace.turn.toolCalls", { n: toolCount }) }}</span>
      <span v-if="hasError" class="ml-auto px-1.5 py-0 rounded bg-coral/15 text-coral text-[10px] font-mono">error</span>
      <span v-if="isCompact" class="px-1.5 py-0 rounded bg-amber/15 text-amber text-[10px] font-mono">compact</span>
    </button>

    <!-- Body -->
    <div v-if="expanded" class="flex flex-col gap-0.5 pl-4 border-l border-warm-100 dark:border-warm-800 ml-1">
      <div v-if="stream.loading && !displayedEvents.length" class="text-[12px] text-secondary px-1 py-1">{{ t("sessionViewer.trace.loading") }}</div>
      <div v-else-if="!displayedEvents.length" class="text-[12px] text-secondary px-1 py-1">{{ t("sessionViewer.trace.turn.empty") }}</div>
      <template v-else>
        <TraceEventRow v-for="(ev, i) in displayedEvents" :key="`${turn.turn_index}-${ev.event_id || i}`" :event="ev" :selected="selectedEventId != null && ev.event_id === selectedEventId" :dimmed="isOutsideFocus(ev)" @select="onSelect" />
        <button v-if="stream.hasMore" class="text-[11px] text-iolite hover:underline self-start px-1 py-1" :disabled="stream.loading" @click="loadMore">{{ stream.loading ? "…" : t("sessionViewer.trace.turn.loadMore") }}</button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from "vue"

import TraceEventRow from "@/components/sessions/trace/TraceEventRow.vue"
import { isTraceErrorEvent } from "@/components/sessions/trace/traceErrors"
import { matchesSearch, parseSearchTerms } from "@/components/sessions/trace/traceSearch"
import { disposeEventStreamStore, useEphemeralEventStreamStore } from "@/stores/eventStream"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  turn: { type: Object, required: true },
  agent: { type: String, default: "" },
  sessionName: { type: String, required: true },
  expanded: { type: Boolean, default: false },
  filters: { type: Object, default: () => ({}) },
  liveEvents: { type: Array, default: () => [] },
  selectedEventId: { type: Number, default: null },
  focusEventIds: { type: Set, default: null },
  jumpEventId: { type: Number, default: null },
  jumpMember: { type: String, default: null },
})
const emit = defineEmits(["toggle", "select-event", "matches", "jumped"])
const rootEl = ref(null)
const streamScope = `trace:${encodeURIComponent(props.sessionName)}:${encodeURIComponent(props.agent)}:${props.turn.turn_index}`
const stream = useEphemeralEventStreamStore(streamScope)

onUnmounted(() => disposeEventStreamStore(streamScope))

function onToggle() {
  emit("toggle", props.turn.turn_index)
}

function onSelect(ev) {
  emit("select-event", ev)
}

// While a timeline interval is focused, events outside it stay visible
// but dimmed — the turn chrome keeps its context.
function isOutsideFocus(ev) {
  const ids = props.focusEventIds
  if (!ids || typeof ev?.event_id !== "number") return false
  // Cluster sessions key focus ids as member:eid (ids are member-local).
  const key = ev.member_sid ? `${ev.member_sid}:${ev.event_id}` : ev.event_id
  return !ids.has(key)
}

const durationS = computed(() => {
  const turn = props.turn
  if (!turn.started_at || !turn.ended_at) return null
  try {
    const start = new Date(turn.started_at).getTime()
    const end = new Date(turn.ended_at).getTime()
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      return Math.round((end - start) / 1000)
    }
  } catch {
    /* ignore */
  }
  return null
})

const tokenStr = computed(() => {
  const tin = Number(props.turn.tokens_in || 0)
  const tout = Number(props.turn.tokens_out || 0)
  const cached = Number(props.turn.tokens_cached || 0)
  if (!tin && !tout && !cached) return ""
  const parts = [`${formatTokens(tin)} in`, `${formatTokens(tout)} out`]
  if (cached) parts.push(`${formatTokens(cached)} cache`)
  return parts.join(" / ")
})

const costStr = computed(() => {
  if (props.turn.cost_usd != null) return `$${Number(props.turn.cost_usd).toFixed(3)}`
  return ""
})

const subagentCount = computed(() => (props.turn.subagent_breakdown || []).length)
const toolCount = computed(() => Number(props.turn.tool_calls || 0))
const hasError = computed(() => Boolean(props.turn.has_error))
const isCompact = computed(() => Boolean(props.turn.compacted))

// Apply filters client-side. Combines store events + the turn's slice
// of live-attach events the parent has buffered.
const displayedEvents = computed(() => {
  const own = stream.events
  const live = (props.liveEvents || []).filter((e) => e.turn_index === props.turn.turn_index)
  const combined = [...own]
  for (const e of live) {
    if (!combined.find((x) => x.event_id === e.event_id)) combined.push(e)
  }
  return combined.filter(_passesFilter)
})

const TYPE_GROUPS = {
  tool: new Set(["tool_call", "tool_result", "tool_error"]),
  subagent: new Set(["subagent_call", "subagent_result", "subagent_error", "subagent_token_usage"]),
  plugin: new Set(["plugin_hook_timing", "plugin_hook"]),
  compact: new Set(["compact_start", "compact_complete", "compact_decision", "compact_replace"]),
  tokens: new Set(["token_usage", "turn_token_usage", "subagent_token_usage"]),
  text: new Set(["text_chunk", "text"]),
  reasoning: new Set(["assistant_reasoning"]),
}

function _passesFilter(ev) {
  const f = props.filters || {}
  if (f.errorsOnly) {
    if (!isTraceErrorEvent(ev)) return false
  }
  const terms = parseSearchTerms(f.search)
  if (terms.length && !matchesSearch(ev, terms)) return false
  const chips = f.typeChips || []
  if (chips.length === 0) return true
  for (const c of chips) {
    const set = TYPE_GROUPS[c]
    if (set && set.has(ev.type)) return true
  }
  return false
}

// While a search is active the parent hides turns with zero matching
// events; report the filtered count so it can decide. Zero is only
// conclusive once pagination is exhausted — a match may live on a later
// event page, so page forward (bounded) before reporting it.
const searchActive = computed(() => parseSearchTerms(props.filters?.search).length > 0)

let searchPageBudget = 10
let searchLastQuery = null

watch(
  () => [searchActive.value, props.filters?.search, props.expanded, stream.loading, stream.turnIndex, displayedEvents.value.length],
  () => {
    if (!searchActive.value || !props.expanded) return
    const query = props.filters?.search || ""
    if (query !== searchLastQuery) {
      searchLastQuery = query
      searchPageBudget = 10
    }
    if (stream.turnIndex !== props.turn.turn_index || stream.loading) return
    const count = displayedEvents.value.length
    if (count > 0) {
      emit("matches", props.turn.turn_index, count)
      return
    }
    if (stream.hasMore && searchPageBudget > 0) {
      searchPageBudget -= 1
      stream.loadMore()
      return
    }
    emit("matches", props.turn.turn_index, 0)
  },
  { immediate: true },
)

// Timeline jump target: once the event row is present, scroll it into
// view and hand the full event object up for highlighting. When the
// turn's events are paginated and the target isn't loaded yet, page
// forward automatically (bounded) until it appears.
let jumpPageBudget = 10
let jumpLastTarget = null

watch(
  () => [props.jumpEventId, displayedEvents.value.length, stream.loading, stream.turnIndex, props.expanded],
  async ([target]) => {
    if (target !== jumpLastTarget) {
      jumpLastTarget = target
      jumpPageBudget = 10
    }
    if (target == null || !props.expanded) return
    // Wait until this turn's events are bound and idle — otherwise a
    // collapsed turn would "exhaust" before its first load even starts.
    if (stream.turnIndex !== props.turn.turn_index || stream.loading) return
    const hit = displayedEvents.value.find((e) => e.event_id === target && (!props.jumpMember || e.member_sid === props.jumpMember))
    if (hit) {
      await nextTick()
      const rows = rootEl.value?.querySelectorAll(`[data-event-id="${target}"]`) ?? []
      const el = [...rows].find((r) => !props.jumpMember || r.dataset.memberSid === props.jumpMember) ?? rows[0]
      el?.scrollIntoView?.({ behavior: "smooth", block: "center" })
      emit("jumped", { event: hit })
      return
    }
    if (jumpPageBudget > 0 && stream.hasMore) {
      jumpPageBudget -= 1
      stream.loadMore()
      return
    }
    // Exhausted or not in this turn — release the target so it isn't sticky.
    jumpPageBudget = 10
    emit("jumped", { event: null })
  },
)

function formatTokens(n) {
  const v = Number(n || 0)
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return String(v)
}

function loadMore() {
  stream.loadMore()
}

watch(
  () => [props.expanded, props.turn.turn_index, props.agent],
  ([exp, ti]) => {
    if (!exp) return
    if (stream.turnIndex !== ti || stream.sessionName !== props.sessionName || stream.agent !== (props.agent || "")) {
      stream.loadTurn(props.sessionName, {
        agent: props.agent || null,
        turnIndex: ti,
      })
    }
  },
  { immediate: true },
)
</script>
