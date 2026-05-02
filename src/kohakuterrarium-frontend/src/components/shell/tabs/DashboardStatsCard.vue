<template>
  <div class="card p-4">
    <header class="flex items-baseline justify-between mb-3">
      <h2 class="text-sm font-medium text-warm-700 dark:text-warm-300">{{ t("shell.statsCard.title") }}</h2>
      <button class="text-xs text-iolite hover:underline" @click="openStats">{{ t("shell.statsCard.openStats") }}</button>
    </header>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
      <Metric :label="t('shell.statsCard.saved')" :value="String(disk.count ?? 0)" />
      <Metric :label="t('shell.statsCard.onDisk')" :value="formatBytes(disk.total_bytes ?? 0)" />
      <Metric :label="t('shell.statsCard.runningLabel')" :value="String(running)" />
      <Metric :label="t('shell.statsCard.last24h')" :value="String(stats.by_recency?.['1d'] ?? 0)" />
    </div>

    <!-- Live process metrics — quick "is it healthy right now?" read.
         Sparklines mirror the StatsTab's full panel; the headline
         number is the last-minute LLM p95 + the last-minute error
         count. Click the card to land on the full Stats tab. -->
    <div class="mt-3 grid grid-cols-2 gap-3 pt-3 border-t border-warm-200/60 dark:border-warm-700/60">
      <div class="flex items-center gap-2">
        <Sparkline :values="metrics.rates?.llm ?? []" :width="64" :height="20" />
        <div class="flex flex-col leading-tight">
          <span class="text-[10px] uppercase tracking-wider text-warm-500">{{ t("shell.statsCard.llmP95") }}</span>
          <span class="font-mono text-sm" :class="llmP95Class">{{ fmtMs(llmP95) }}</span>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <Sparkline :values="metrics.rates?.error ?? []" :width="64" :height="20" :stroke-color="errorRate > 0 ? '#D46B6B' : '#5A4FCF'" :fill-color="errorRate > 0 ? '#D46B6B' : '#5A4FCF'" />
        <div class="flex flex-col leading-tight">
          <span class="text-[10px] uppercase tracking-wider text-warm-500">{{ t("shell.statsCard.errorsPer5m") }}</span>
          <span class="font-mono text-sm" :class="errorRate > 0 ? 'text-coral' : 'text-warm-700 dark:text-warm-300'">{{ errorRate }}</span>
        </div>
      </div>
    </div>

    <div class="mt-3 flex items-center gap-3 text-[11px] text-warm-500">
      <span class="flex items-center gap-1.5">
        <span class="w-1.5 h-1.5 rounded-full bg-aquamarine" />
        {{ t("shell.statsCard.providersReady", { count: providersOk }) }}
      </span>
      <span class="flex items-center gap-1.5">
        <span class="w-1.5 h-1.5 rounded-full bg-warm-400" />
        {{ t("shell.statsCard.providersUnconfigured", { count: providersOff }) }}
      </span>
      <span class="ml-auto">{{ t("shell.statsCard.mcpServers", { count: mcpCount }) }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue"

import Metric from "@/components/common/Metric.vue"
import Sparkline from "@/components/common/Sparkline.vue"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"
import { settingsAPI, statsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const tabs = useTabsStore()
const instances = useInstancesStore()

const disk = ref({})
const stats = ref({})
const providers = ref([])
const mcp = ref([])
const metrics = ref({})
let metricsTimer = null

async function loadMetrics() {
  try {
    metrics.value = await statsAPI.metrics()
  } catch {
    // soft-fail; keep stale data on transient errors
  }
}

onMounted(async () => {
  await Promise.all([
    statsAPI
      .diskUsage()
      .then((r) => (disk.value = r))
      .catch(() => {}),
    statsAPI
      .sessionStats()
      .then((r) => (stats.value = r))
      .catch(() => {}),
    settingsAPI
      .getBackends()
      .then((r) => {
        const list = Array.isArray(r) ? r : (r?.backends ?? [])
        providers.value = list
      })
      .catch(() => {}),
    settingsAPI
      .listMCP()
      .then((r) => {
        const list = Array.isArray(r) ? r : (r?.servers ?? r?.items ?? [])
        mcp.value = Array.isArray(list) ? list : []
      })
      .catch(() => {}),
    loadMetrics(),
  ])
  // 5 s refresh — same cadence as the rest of the dashboard so the
  // user feels one heartbeat, not a flicker of independent updates.
  metricsTimer = setInterval(loadMetrics, 5000)
})

onUnmounted(() => {
  if (metricsTimer) clearInterval(metricsTimer)
})

const running = computed(() => instances.list.filter((i) => i.status === "running").length)

const providersOk = computed(() => providers.value.filter((p) => p.has_key === true || p.available === true).length)

const providersOff = computed(() => providers.value.length - providersOk.value)

const mcpCount = computed(() => mcp.value.length)

const llmP95 = computed(() => {
  const series = metrics.value?.histograms?.llm_response_ms
  if (!series) return 0
  // Take the worst p95 across all (provider, model) pairs over the
  // last 5 minutes. The mini-strip is a "should I be worried" pulse
  // — the worst-case is more useful than the average.
  let max = 0
  for (const key of Object.keys(series)) {
    const win = series[key]?.["5m"]
    if (win?.p95_ms > max) max = win.p95_ms
  }
  return max
})

const llmP95Class = computed(() => {
  if (llmP95.value > 5000) return "text-coral"
  if (llmP95.value > 1500) return "text-amber"
  return "text-warm-700 dark:text-warm-300"
})

const errorRate = computed(() => {
  const series = metrics.value?.rates?.error || []
  return series.reduce((a, b) => a + (Number(b) || 0), 0)
})

function fmtMs(ms) {
  if (!ms) return "—"
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function openStats() {
  tabs.openTab({ kind: "stats", id: "stats" })
}

function formatBytes(b) {
  if (!b) return "0 B"
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`
}
</script>
