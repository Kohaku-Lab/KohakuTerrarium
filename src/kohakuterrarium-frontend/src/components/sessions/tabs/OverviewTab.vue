<template>
  <div class="h-full min-h-0 overflow-y-auto">
    <div v-if="detail.loadingSummary && !detail.summary" class="card p-6 text-secondary text-sm">{{ t("common.loading") }}</div>
    <div v-else-if="!detail.summary" class="card p-6 text-secondary text-sm">{{ t("sessionViewer.overview.empty") }}</div>

    <div v-else class="flex flex-col gap-3">
      <!-- Header card: timestamps + lineage + status -->
      <div class="card p-4 flex flex-col gap-2">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12px]">
          <Field :label="t('sessionViewer.overview.started')" :value="formatDate(summary.created_at)" />
          <Field :label="t('sessionViewer.overview.lastActive')" :value="formatDate(summary.last_active)" />
          <Field :label="t('sessionViewer.overview.status')">
            <span :class="statusClass">{{ summary.status || "—" }}</span>
          </Field>
          <Field :label="t('sessionViewer.overview.format')" :value="`v${summary.format_version || 1}`" />
        </div>
        <div v-if="lineageDescription" class="text-[12px] text-warm-600 dark:text-warm-400">
          <span class="text-warm-400 mr-1">{{ t("sessionViewer.overview.lineage") }}:</span> {{ lineageDescription }}
        </div>
      </div>

      <!-- Counts -->
      <div class="card p-4 flex flex-col gap-2">
        <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.overview.counts") }}</div>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[13px]">
          <Stat :label="t('sessionViewer.overview.turns')" :value="totals.turns" />
          <Stat :label="t('sessionViewer.overview.toolCalls')" :value="totals.tool_calls" />
          <Stat :label="t('sessionViewer.overview.errors')" :value="totals.errors" tone="error" />
          <Stat :label="t('sessionViewer.overview.compacts')" :value="totals.compacts" />
          <Stat :label="t('sessionViewer.overview.forks')" :value="totals.forks" />
          <Stat :label="t('sessionViewer.overview.attached')" :value="totals.attached_agents" />
        </div>
      </div>

      <!-- Tokens + cost -->
      <div class="card p-4 flex flex-col gap-2">
        <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.overview.tokens") }} / {{ t("sessionViewer.overview.cost") }}</div>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[13px]">
          <Stat :label="t('common.in')" :value="formatTokens(totals.tokens && totals.tokens.prompt)" />
          <Stat :label="t('common.completion')" :value="formatTokens(totals.tokens && totals.tokens.completion)" />
          <Stat :label="t('common.cached')" :value="formatTokens(totals.tokens && totals.tokens.cached)" />
          <Stat :label="t('sessionViewer.overview.cost')" :value="totals.cost_usd != null ? `$${totals.cost_usd.toFixed(2)}` : t('sessionViewer.overview.noCost')" />
        </div>
      </div>

      <!-- Hot turns -->
      <div v-if="summary.hot_turns && summary.hot_turns.length" class="card p-4 flex flex-col gap-2">
        <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.overview.hotTurns") }}</div>
        <div class="grid grid-cols-1 gap-1.5">
          <button v-for="ht in summary.hot_turns" :key="`${ht.agent}:${ht.turn_index}`" class="text-left flex items-center gap-2 text-[12px] px-2 py-1.5 rounded hover:bg-warm-100 dark:hover:bg-warm-800" @click="openTurn(ht)">
            <span class="font-mono text-warm-500 w-12 shrink-0">#{{ ht.turn_index }}</span>
            <span class="font-mono text-warm-400 w-20 shrink-0 truncate">{{ ht.agent }}</span>
            <span class="text-warm-700 dark:text-warm-300">{{ ht.cost_usd != null ? `$${Number(ht.cost_usd).toFixed(3)}` : `${formatTokens((Number(ht.tokens_in) || 0) + (Number(ht.tokens_out) || 0))} tok` }}</span>
          </button>
        </div>
      </div>

      <!-- Actions row -->
      <div class="card p-3 flex flex-wrap items-center gap-2 text-[12px]">
        <el-dropdown trigger="click" @command="onExport">
          <el-button size="small" plain>
            <span class="i-carbon-export mr-1" />{{ t("sessionViewer.export.menu") }}
            <span class="i-carbon-chevron-down ml-1 text-[10px]" />
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="md">{{ t("sessionViewer.export.markdown") }}</el-dropdown-item>
              <el-dropdown-item command="html">{{ t("sessionViewer.export.html") }}</el-dropdown-item>
              <el-dropdown-item command="jsonl">{{ t("sessionViewer.export.jsonl") }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <!-- Errors / compactions index -->
      <div v-if="(summary.error_turns || []).length || (summary.compact_turns || []).length" class="card p-4 flex flex-col gap-2 text-[12px]">
        <div class="text-[11px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.overview.errors") }} / {{ t("sessionViewer.overview.compacts") }}</div>
        <div v-if="(summary.error_turns || []).length" class="flex flex-wrap gap-1.5 items-center">
          <span class="text-coral text-[11px] mr-1">{{ t("sessionViewer.overview.errors") }}:</span>
          <button v-for="ti in summary.error_turns" :key="`err-${ti}`" class="px-1.5 py-0.5 rounded bg-coral/10 text-coral hover:bg-coral/20 font-mono text-[11px]" @click="openTurn({ turn_index: ti })">#{{ ti }}</button>
        </div>
        <div v-if="(summary.compact_turns || []).length" class="flex flex-wrap gap-1.5 items-center">
          <span class="text-amber text-[11px] mr-1">{{ t("sessionViewer.overview.compacts") }}:</span>
          <button v-for="ti in summary.compact_turns" :key="`comp-${ti}`" class="px-1.5 py-0.5 rounded bg-amber/10 text-amber hover:bg-amber/20 font-mono text-[11px]" @click="openTurn({ turn_index: ti })">#{{ ti }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h } from "vue"
import { useRoute, useRouter } from "vue-router"

import { useSessionDetailStore } from "@/stores/sessionDetail"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const detail = useSessionDetailStore()
const router = useRouter()
const route = useRoute()

const summary = computed(() => detail.summary || {})
const totals = computed(() => summary.value.totals || {})

const lineageDescription = computed(() => {
  const lin = summary.value.lineage
  if (!lin || typeof lin !== "object") return ""
  const fork = lin.fork
  if (fork && fork.parent_session_id) {
    return t("sessionViewer.overview.lineageForkedFrom", {
      parent: fork.parent_session_id,
      at: fork.fork_point ?? "?",
    })
  }
  return ""
})

const statusClass = computed(() => {
  const s = (summary.value.status || "").toLowerCase()
  if (s === "running") return "text-aquamarine"
  if (s === "paused") return "text-warm-500"
  if (s === "crashed" || s === "error") return "text-coral"
  return "text-warm-500"
})

function formatDate(iso) {
  if (!iso) return "—"
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function formatTokens(n) {
  const v = Number(n || 0)
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return v.toString()
}

function openTurn(ht) {
  if (!ht || ht.turn_index == null) return
  detail.setTab("trace")
  router.replace({
    query: { ...route.query, tab: "trace", turn: ht.turn_index },
  })
}

function onExport(format) {
  if (!detail.name) return
  // Navigate to the streaming endpoint — the backend stamps a
  // ``Content-Disposition: attachment`` header so the browser hands
  // off to the standard download dialog.
  const url = sessionAPI.exportUrl(detail.name, format)
  window.location.assign(url)
}

// Inline tiny presentational components.
const Field = (props, { slots }) => {
  return h("div", { class: "flex flex-col gap-0.5" }, [h("span", { class: "text-[10px] uppercase tracking-wider text-warm-400" }, props.label), h("span", { class: "text-warm-700 dark:text-warm-300" }, slots.default ? slots.default() : props.value || "—")])
}
Field.props = ["label", "value"]

const Stat = (props) => {
  const toneClass = props.tone === "error" ? (Number(props.value) > 0 ? "text-coral" : "text-warm-500") : "text-warm-700 dark:text-warm-300"
  return h("div", { class: "flex flex-col gap-0.5" }, [h("span", { class: "text-[10px] uppercase tracking-wider text-warm-400" }, props.label), h("span", { class: `font-medium ${toneClass}` }, String(props.value ?? "—"))])
}
Stat.props = ["label", "value", "tone"]
</script>
