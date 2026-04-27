<template>
  <div class="card p-2 flex flex-col gap-1">
    <!-- Header row -->
    <button class="w-full flex items-center gap-2 text-[12px] text-left py-1 px-1 rounded hover:bg-warm-50 dark:hover:bg-warm-800/50" @click="onToggle">
      <span class="i-carbon-chevron-right transition-transform shrink-0" :class="expanded ? 'rotate-90 text-iolite' : 'text-warm-400'" />
      <span class="font-mono w-16 shrink-0 text-warm-700 dark:text-warm-300">#{{ turn.turn_index }}</span>
      <span v-if="agent" class="font-mono text-warm-400 w-20 shrink-0 truncate">{{ agent }}</span>
      <span v-if="durationS != null" class="text-warm-500 shrink-0">{{ t("sessionViewer.trace.turn.duration", { s: durationS }) }}</span>
      <span v-if="costStr" class="text-warm-700 dark:text-warm-300 shrink-0">{{ costStr }}</span>
      <span v-if="toolCount" class="text-warm-500 shrink-0">{{ t("sessionViewer.trace.turn.toolCalls", { n: toolCount }) }}</span>
      <span v-if="hasError" class="ml-auto px-1.5 py-0 rounded bg-coral/15 text-coral text-[10px] font-mono">error</span>
      <span v-if="isCompact" class="px-1.5 py-0 rounded bg-amber/15 text-amber text-[10px] font-mono">compact</span>
    </button>

    <!-- Body -->
    <div v-if="expanded" class="flex flex-col gap-0.5 pl-4 border-l border-warm-100 dark:border-warm-800 ml-1">
      <div v-if="stream.loading && !displayedEvents.length" class="text-[12px] text-secondary px-1 py-1">{{ t("sessionViewer.trace.loading") }}</div>
      <div v-else-if="!displayedEvents.length" class="text-[12px] text-secondary px-1 py-1">{{ t("sessionViewer.trace.turn.empty") }}</div>
      <template v-else>
        <TraceEventRow v-for="(ev, i) in displayedEvents" :key="`${turn.turn_index}-${ev.event_id || i}`" :event="ev" :selected="selectedEventId != null && ev.event_id === selectedEventId" @select="onSelect" />
        <button v-if="stream.hasMore" class="text-[11px] text-iolite hover:underline self-start px-1 py-1" :disabled="stream.loading" @click="loadMore">{{ stream.loading ? "…" : t("sessionViewer.trace.turn.loadMore") }}</button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from "vue"

import TraceEventRow from "@/components/sessions/trace/TraceEventRow.vue"
import { useEventStreamStore } from "@/stores/eventStream"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const stream = useEventStreamStore()

const props = defineProps({
  turn: { type: Object, required: true },
  agent: { type: String, default: "" },
  sessionName: { type: String, required: true },
  expanded: { type: Boolean, default: false },
  filters: { type: Object, default: () => ({}) },
  liveEvents: { type: Array, default: () => [] },
  selectedEventId: { type: Number, default: null },
})
const emit = defineEmits(["toggle", "select-event"])

function onToggle() {
  emit("toggle", props.turn.turn_index)
}

function onSelect(ev) {
  emit("select-event", ev)
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

const costStr = computed(() => {
  if (props.turn.cost_usd != null) return `$${Number(props.turn.cost_usd).toFixed(3)}`
  return ""
})

const toolCount = computed(() => Number(props.turn.tool_calls || 0))
const hasError = computed(() => Boolean(props.turn.has_error))
const isCompact = computed(() => Boolean(props.turn.compacted))

// Apply filters client-side. Combines store events + the turn's slice
// of live-attach events the parent has buffered.
const displayedEvents = computed(() => {
  const own = isThisTurnInStore() ? stream.events : []
  const live = (props.liveEvents || []).filter((e) => e.turn_index === props.turn.turn_index)
  const combined = [...own]
  for (const e of live) {
    if (!combined.find((x) => x.event_id === e.event_id)) combined.push(e)
  }
  return combined.filter(_passesFilter)
})

function isThisTurnInStore() {
  return stream.turnIndex === props.turn.turn_index
}

const TYPE_GROUPS = {
  tool: new Set(["tool_call", "tool_result", "tool_error"]),
  subagent: new Set(["subagent_call", "subagent_result", "subagent_error"]),
  plugin: new Set(["plugin_hook_timing", "plugin_hook"]),
  compact: new Set(["compact_start", "compact_complete", "compact_decision", "compact_replace"]),
  tokens: new Set(["token_usage", "turn_token_usage"]),
  text: new Set(["text_chunk", "text"]),
}

function _passesFilter(ev) {
  const f = props.filters || {}
  if (f.errorsOnly) {
    if (!String(ev.type || "").includes("error")) return false
  }
  const chips = f.typeChips || []
  if (chips.length === 0) return true
  for (const c of chips) {
    const set = TYPE_GROUPS[c]
    if (set && set.has(ev.type)) return true
  }
  return false
}

function loadMore() {
  stream.loadMore()
}

watch(
  () => [props.expanded, props.turn.turn_index, props.agent],
  ([exp, ti]) => {
    if (!exp) return
    if (stream.turnIndex !== ti || stream.sessionName !== props.sessionName) {
      stream.loadTurn(props.sessionName, {
        agent: props.agent || null,
        turnIndex: ti,
      })
    }
  },
  { immediate: true },
)
</script>
