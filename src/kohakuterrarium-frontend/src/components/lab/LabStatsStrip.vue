<template>
  <button type="button" class="kt-v2-chrome kt-v2-line border-t w-full h-8 shrink-0 flex items-center gap-4 px-4 text-[11px] text-warm-500 overflow-x-auto whitespace-nowrap hover:text-warm-700 dark:hover:text-warm-300" :title="t('lab.stats.open')" data-test="lab-stats" @click="tabs.openTab({ kind: 'stats', id: 'stats' })">
    <span v-for="s in items" :key="s.id" class="flex items-center gap-1.5" :data-test="`lab-stat-${s.id}`">
      <span :class="s.icon" />
      <span class="font-mono tabular-nums" :class="s.tone || 'text-warm-700 dark:text-warm-200'">{{ s.value }}</span>
      <span>{{ s.label }}</span>
    </span>
    <span class="flex-1" />
    <span class="i-carbon-chart-line" />
  </button>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useTabsStore } from "@/stores/tabs"
import { statsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * One line of numbers under the bench: what runs now (from the tanks),
 * what is saved, and how the model calls are doing. Click for the Stats page.
 */
const props = defineProps({ tanks: { type: Array, default: () => [] } })

const METRICS_MS = 10000
const { t } = useI18n()
const tabs = useTabsStore()
const disk = ref({})
const metrics = ref({})
let timer = null

async function loadMetrics() {
  try {
    metrics.value = await statsAPI.metrics()
  } catch {
    /* keep the last numbers */
  }
}

function formatBytes(b) {
  if (!b) return "0 B"
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

const p95 = computed(() => {
  let max = 0
  for (const win of Object.values(metrics.value?.histograms?.llm_response_ms || {})) max = Math.max(max, win?.["5m"]?.p95_ms || 0)
  return max
})
const errors = computed(() => (metrics.value?.rates?.error || []).reduce((a, b) => a + (Number(b) || 0), 0))

const items = computed(() => {
  const creatures = props.tanks.reduce((n, tk) => n + tk.size, 0)
  const busy = props.tanks.reduce((n, tk) => n + (tk.counts.busy || 0), 0)
  const machines = new Set(props.tanks.flatMap((tk) => tk.hosts)).size
  const out = [
    { id: "running", icon: "i-carbon-network-4", value: props.tanks.length, label: t("lab.stats.running") },
    { id: "creatures", icon: "i-carbon-bot", value: creatures, label: t("lab.stats.creatures") },
    { id: "busy", icon: "i-carbon-circle-filled", value: busy, label: t("lab.stats.busy"), tone: busy ? "text-aquamarine-shadow dark:text-aquamarine-light" : "" },
  ]
  if (machines > 1) out.push({ id: "machines", icon: "i-carbon-bare-metal-server", value: machines, label: t("lab.stats.machines") })
  out.push({ id: "saved", icon: "i-carbon-recently-viewed", value: disk.value.count ?? "—", label: t("lab.stats.saved") }, { id: "disk", icon: "i-carbon-data-base", value: formatBytes(disk.value.total_bytes), label: t("lab.stats.disk") }, { id: "p95", icon: "i-carbon-time", value: p95.value ? `${(p95.value / 1000).toFixed(1)}s` : "—", label: t("lab.stats.p95"), tone: p95.value > 5000 ? "text-coral" : p95.value > 1500 ? "text-amber" : "" }, { id: "errors", icon: "i-carbon-warning-alt", value: errors.value, label: t("lab.stats.errors"), tone: errors.value ? "text-coral" : "" })
  return out
})

onMounted(() => {
  statsAPI
    .diskUsage()
    .then((r) => (disk.value = r || {}))
    .catch(() => {})
  loadMetrics()
  timer = createVisibilityInterval(() => loadMetrics(), METRICS_MS)
  timer.start()
})
onBeforeUnmount(() => timer?.stop())
</script>
