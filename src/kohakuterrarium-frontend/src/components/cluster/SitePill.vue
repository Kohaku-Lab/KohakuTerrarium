<template>
  <button v-if="show" type="button" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium leading-none transition-colors shrink-0" :class="pillClasses" :title="title" @click="$emit('click', $event)">
    <span class="i-carbon-network-3 w-3 h-3 shrink-0" />
    <span v-if="siteCount === 1">{{ t("cluster.pill.hostOnly") }}</span>
    <span v-else>{{ t("cluster.pill.sites", { n: siteCount }) }}</span>
  </button>
</template>

<script setup>
import { computed } from "vue"

import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

defineEmits(["click"])

const cluster = useClusterStore()
const { t } = useI18n()

// Show only in lab-host mode.  Standalone clients render nothing.
const show = computed(() => cluster.isCluster)
const siteCount = computed(() => cluster.siteCount)

const pillClasses = computed(() => {
  if (siteCount.value === 1) {
    // Host-only: muted grey badge — no real cluster to brag about.
    return "bg-warm-200 text-warm-600 dark:bg-warm-800 dark:text-warm-300 hover:bg-warm-300 dark:hover:bg-warm-700"
  }
  // Active cluster (≥2 sites): iolite accent matches "running" status
  // chips used elsewhere in the rail.
  return "bg-iolite/15 text-iolite-dark dark:text-iolite-light hover:bg-iolite/25"
})

const title = computed(() => {
  if (siteCount.value === 1) return t("cluster.pill.hostOnly")
  return t("cluster.pill.tooltip", { n: siteCount.value })
})
</script>
