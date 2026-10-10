<template>
  <button type="button" class="w-full flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-warm-500 text-left hover:text-warm-700 dark:hover:text-warm-300" :title="t('lab.stats.open')" data-test="lab-stats" @click="tabs.openTab({ kind: 'stats', id: 'stats' })">
    <span v-for="s in items" :key="s.id" class="flex items-center gap-1" :data-test="`lab-stat-${s.id}`">
      <span class="font-mono" :class="s.tone || 'text-warm-700 dark:text-warm-200'">{{ s.value }}</span>
      <span>{{ s.label }}</span>
    </span>
  </button>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useTabsStore } from "@/stores/tabs"
import { statsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/** The lab's numbers in one quiet line: saved sessions, disk, model p95 and recent errors; opens the Stats page. */
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
const items = computed(() => [
  { id: "saved", value: disk.value.count ?? "—", label: t("lab.stats.saved") },
  { id: "disk", value: formatBytes(disk.value.total_bytes), label: t("lab.stats.disk") },
  { id: "p95", value: p95.value ? `${(p95.value / 1000).toFixed(1)}s` : "—", label: t("lab.stats.p95"), tone: p95.value > 5000 ? "text-coral" : p95.value > 1500 ? "text-amber" : "" },
  { id: "errors", value: errors.value, label: t("lab.stats.errors"), tone: errors.value ? "text-coral" : "" },
])

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
