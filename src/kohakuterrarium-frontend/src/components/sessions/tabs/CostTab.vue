<template>
  <div class="h-full min-h-0 overflow-y-auto p-4 flex flex-col gap-4">
    <!-- Header row: aggregate toggle (default ON) + agent picker (when single-mode) -->
    <div class="flex flex-wrap items-center gap-3 text-[12px]">
      <el-checkbox v-model="aggregateMode">{{ t("sessionViewer.cost.aggregate") }}</el-checkbox>
      <div v-if="!aggregateMode && agents.length > 1" class="flex items-center gap-2">
        <span class="text-warm-400">{{ t("sessionViewer.trace.filters.agent") }}:</span>
        <el-select v-model="agent" size="small" style="width: 160px">
          <el-option v-for="a in agents" :key="a" :value="a" :label="a" />
        </el-select>
      </div>
      <span class="ml-auto text-warm-400 text-[11px]">{{ aggregateMode ? t("sessionViewer.cost.aggregateHint") : "" }}</span>
    </div>

    <!-- Loading / empty -->
    <div v-if="rollup.loading && !turns.length" class="card p-4 text-secondary text-sm">{{ t("common.loading") }}</div>
    <div v-else-if="!turns.length" class="card p-4 text-secondary text-sm">{{ t("sessionViewer.trace.empty") }}</div>

    <template v-else>
      <!-- Totals (now includes cached) -->
      <div class="card p-4 grid grid-cols-2 sm:grid-cols-5 gap-3 text-[13px]">
        <Stat :label="t('sessionViewer.overview.turns')" :value="String(turns.length)" />
        <Stat :label="t('common.in')" :value="formatTokens(totalIn)" />
        <Stat :label="t('common.completion')" :value="formatTokens(totalOut)" />
        <Stat :label="t('common.cached')" :value="formatTokens(totalCached)" />
        <Stat :label="t('sessionViewer.cost.total')" :value="costAvailable ? `$${totalCost.toFixed(2)}` : t('sessionViewer.overview.noCost')" />
      </div>

      <!-- Per-turn bar chart -->
      <div class="card p-3 flex flex-col gap-2">
        <div class="flex items-center text-[11px] text-warm-400">
          <span>{{ costAvailable ? t("sessionViewer.cost.unit.cost") : t("sessionViewer.cost.unit.tokens") }}</span>
          <span class="ml-auto font-mono">turn 1 – {{ lastTurnIndex }}</span>
        </div>
        <div class="flex items-end gap-px h-32 min-w-0">
          <button v-for="t2 in turns" :key="t2.turn_index" class="flex-1 min-w-[3px] rounded-t transition-colors" :class="barClass(t2)" :style="{ height: heightFor(t2) }" :title="tooltipFor(t2)" @click="toggleTurn(t2.turn_index)" />
        </div>
      </div>

      <!-- All-turns list with expandable per-agent breakdown -->
      <div class="card p-3 flex flex-col gap-1">
        <div class="text-[11px] uppercase tracking-wider text-warm-400 mb-1">{{ t("sessionViewer.cost.perTurn") }}</div>
        <div class="grid grid-cols-[3rem_1fr_5rem_5rem_5rem_5rem_2rem] items-center gap-2 text-[10px] text-warm-400 px-1 pb-1 border-b border-warm-200 dark:border-warm-700">
          <span>#</span>
          <span></span>
          <span class="text-right">{{ t("common.in") }}</span>
          <span class="text-right">{{ t("common.completion") }}</span>
          <span class="text-right">{{ t("common.cached") }}</span>
          <span class="text-right">{{ t("sessionViewer.cost.unit.cost") }}</span>
          <span></span>
        </div>
        <template v-for="t2 in turns" :key="t2.turn_index">
          <button class="grid grid-cols-[3rem_1fr_5rem_5rem_5rem_5rem_2rem] items-center gap-2 text-[12px] text-left px-1 py-1.5 rounded hover:bg-warm-50 dark:hover:bg-warm-800/50" @click="toggleTurn(t2.turn_index)">
            <span class="font-mono text-warm-500">#{{ t2.turn_index }}</span>
            <span class="flex items-center gap-2 min-w-0">
              <span v-if="t2.has_error" class="px-1 py-0 rounded bg-coral/15 text-coral text-[10px] font-mono">err</span>
              <span v-if="t2.compacted" class="px-1 py-0 rounded bg-amber/15 text-amber text-[10px] font-mono">compact</span>
              <span v-if="(t2.breakdown || []).length > 1" class="text-warm-400 text-[10px]">{{ t("sessionViewer.cost.nAgents", { n: t2.breakdown.length }) }}</span>
            </span>
            <span class="text-right font-mono text-warm-700 dark:text-warm-300">{{ formatTokens(t2.tokens_in) }}</span>
            <span class="text-right font-mono text-warm-700 dark:text-warm-300">{{ formatTokens(t2.tokens_out) }}</span>
            <span class="text-right font-mono" :class="t2.tokens_cached ? 'text-aquamarine' : 'text-warm-400'">{{ formatTokens(t2.tokens_cached) }}</span>
            <span class="text-right font-mono text-warm-700 dark:text-warm-300">{{ t2.cost_usd != null ? `$${Number(t2.cost_usd).toFixed(3)}` : "—" }}</span>
            <span class="i-carbon-chevron-right transition-transform shrink-0 justify-self-end" :class="expandedTurns.has(t2.turn_index) ? 'rotate-90 text-iolite' : 'text-warm-400'" />
          </button>
          <!-- Per-agent breakdown when expanded -->
          <div v-if="expandedTurns.has(t2.turn_index) && (t2.breakdown || []).length" class="ml-4 mb-2 pl-3 border-l border-warm-200 dark:border-warm-700 flex flex-col gap-0.5">
            <div v-for="(b, i) in t2.breakdown" :key="`${t2.turn_index}-${b.agent}-${i}`" class="grid grid-cols-[3rem_1fr_5rem_5rem_5rem_5rem_2rem] items-center gap-2 text-[11px] py-0.5">
              <span class="font-mono text-[10px]" :class="b.kind === 'attached' ? 'text-aquamarine' : 'text-warm-400'">{{ b.kind }}</span>
              <span class="font-mono text-warm-500 dark:text-warm-400 truncate">{{ b.agent }}</span>
              <span class="text-right font-mono text-warm-600 dark:text-warm-400">{{ formatTokens(b.tokens_in) }}</span>
              <span class="text-right font-mono text-warm-600 dark:text-warm-400">{{ formatTokens(b.tokens_out) }}</span>
              <span class="text-right font-mono" :class="b.tokens_cached ? 'text-aquamarine' : 'text-warm-400'">{{ formatTokens(b.tokens_cached) }}</span>
              <span class="text-right font-mono text-warm-600 dark:text-warm-400">{{ b.cost_usd != null ? `$${Number(b.cost_usd).toFixed(3)}` : "—" }}</span>
              <span></span>
            </div>
          </div>
        </template>
      </div>

      <!-- Top spenders for quick navigation -->
      <div v-if="topSpenders.length" class="card p-3 flex flex-col gap-2">
        <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.cost.topSpenders") }}</div>
        <div class="flex flex-col gap-1">
          <button v-for="ht in topSpenders" :key="ht.turn_index" class="text-left flex items-center gap-2 text-[12px] px-2 py-1.5 rounded hover:bg-warm-100 dark:hover:bg-warm-800" @click="openTurn(ht.turn_index)">
            <span class="font-mono text-warm-500 w-12 shrink-0">#{{ ht.turn_index }}</span>
            <span class="flex-1 min-w-0 text-warm-700 dark:text-warm-300">{{ ht.cost_usd != null ? `$${Number(ht.cost_usd).toFixed(3)}` : `${formatTokens((Number(ht.tokens_in) || 0) + (Number(ht.tokens_out) || 0))} tok` }}</span>
            <span class="font-mono text-warm-400 shrink-0 text-[11px]">{{ formatTokens(ht.tokens_in) }} in / {{ formatTokens(ht.tokens_out) }} out / {{ formatTokens(ht.tokens_cached || 0) }} cache</span>
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, h, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useTurnRollupStore } from "@/stores/turnRollup"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const detail = useSessionDetailStore()
const rollup = useTurnRollupStore()
const router = useRouter()
const route = useRoute()

const aggregateMode = ref(true)
const agent = ref("")
const expandedTurns = ref(new Set())

const agents = computed(() => detail.agents || [])
const turns = computed(() => rollup.turns)
const lastTurnIndex = computed(() => (turns.value.length ? turns.value[turns.value.length - 1].turn_index : 0))

const totalIn = computed(() => turns.value.reduce((s, r) => s + (Number(r.tokens_in) || 0), 0))
const totalOut = computed(() => turns.value.reduce((s, r) => s + (Number(r.tokens_out) || 0), 0))
const totalCached = computed(() => turns.value.reduce((s, r) => s + (Number(r.tokens_cached) || 0), 0))
const totalCost = computed(() => turns.value.reduce((s, r) => s + (Number(r.cost_usd) || 0), 0))
const costAvailable = computed(() => turns.value.some((r) => r.cost_usd != null))

const maxCost = computed(() => Math.max(...turns.value.map((r) => Number(r.cost_usd) || 0), 1e-9))
const maxTok = computed(() => Math.max(...turns.value.map((r) => (Number(r.tokens_in) || 0) + (Number(r.tokens_out) || 0)), 1))

const topSpenders = computed(() => {
  const ranked = [...turns.value].sort((a, b) => {
    if (costAvailable.value) return (Number(b.cost_usd) || 0) - (Number(a.cost_usd) || 0)
    const av = (Number(a.tokens_in) || 0) + (Number(a.tokens_out) || 0)
    const bv = (Number(b.tokens_in) || 0) + (Number(b.tokens_out) || 0)
    return bv - av
  })
  return ranked.slice(0, 5)
})

function heightFor(turn) {
  const v = costAvailable.value ? (Number(turn.cost_usd) || 0) / maxCost.value : ((Number(turn.tokens_in) || 0) + (Number(turn.tokens_out) || 0)) / maxTok.value
  return `${Math.max(2, Math.min(100, v * 100))}%`
}

function barClass(turn) {
  if (turn.has_error) return "bg-coral hover:bg-coral/90"
  return "bg-iolite/70 hover:bg-iolite"
}

function tooltipFor(turn) {
  const parts = [`#${turn.turn_index}`]
  if (turn.cost_usd != null) parts.push(`$${Number(turn.cost_usd).toFixed(3)}`)
  parts.push(`${turn.tokens_in || 0} in / ${turn.tokens_out || 0} out / ${turn.tokens_cached || 0} cached`)
  if (turn.breakdown?.length) parts.push(`${turn.breakdown.length} agent${turn.breakdown.length === 1 ? "" : "s"}`)
  return parts.join("  ·  ")
}

function formatTokens(n) {
  const v = Number(n || 0)
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return String(v)
}

function toggleTurn(ti) {
  const next = new Set(expandedTurns.value)
  if (next.has(ti)) next.delete(ti)
  else next.add(ti)
  expandedTurns.value = next
}

function openTurn(turnIndex) {
  detail.setTab("trace")
  router.replace({ query: { ...route.query, tab: "trace", turn: turnIndex } })
}

watch(
  () => [detail.name, agent.value, agents.value, aggregateMode.value],
  async ([name, a, list, agg]) => {
    if (!name) return
    const target = agg ? null : a || list[0] || null
    if (!agg && !a && list.length) agent.value = list[0]
    await rollup.load(name, target, { aggregate: agg })
    expandedTurns.value = new Set()
  },
  { immediate: true },
)

const Stat = (props) => {
  return h("div", { class: "flex flex-col gap-0.5" }, [h("span", { class: "text-[10px] uppercase tracking-wider text-warm-400" }, props.label), h("span", { class: "font-medium text-warm-700 dark:text-warm-300" }, String(props.value ?? "—"))])
}
Stat.props = ["label", "value"]
</script>
