<template>
  <span v-if="!hidden" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium leading-none shrink-0" :class="chipClasses" :title="title">
    <span class="w-1.5 h-1.5 rounded-full" :class="dotColor" />
    <span class="truncate">{{ label }}</span>
  </span>
</template>

<script setup>
import { computed } from "vue"

import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  /** Wire-side node_id ("_host" / "worker-1"). */
  nodeId: { type: String, default: "" },
  /**
   * Hide the chip in standalone mode (most common usage in tables).
   * Pass ``always`` to force-render — used in Settings → Sites.
   */
  alwaysShow: { type: Boolean, default: false },
})

const cluster = useClusterStore()
const { t } = useI18n()

const hidden = computed(() => {
  if (props.alwaysShow) return false
  // Hide in standalone or single-site labs — chip carries no info.
  return !cluster.isCluster || cluster.siteCount < 2
})

const isHost = computed(() => props.nodeId === "_host" || cluster.getSite(props.nodeId)?.isHost)

const label = computed(() => (isHost.value ? t("cluster.site.host") : props.nodeId))

const palette = computed(() => cluster.colorFor(props.nodeId))

const chipClasses = computed(() => {
  // The cluster store assigns one of these palette tokens.
  // Frontend palette: neutral / teal / amber / iolite / rose / violet / cyan / lime.
  switch (palette.value) {
    case "neutral":
      return "bg-warm-200 text-warm-700 dark:bg-warm-800 dark:text-warm-300"
    case "teal":
      return "bg-teal/15 text-teal-shadow dark:text-teal-light"
    case "amber":
      return "bg-amber/15 text-amber-shadow dark:text-amber-light"
    case "iolite":
      return "bg-iolite/15 text-iolite-shadow dark:text-iolite-light"
    case "rose":
      return "bg-rose/15 text-rose-shadow dark:text-rose-light"
    case "violet":
      return "bg-violet/15 text-violet-shadow dark:text-violet-light"
    case "cyan":
      return "bg-cyan/15 text-cyan-shadow dark:text-cyan-light"
    case "lime":
      return "bg-lime/15 text-lime-shadow dark:text-lime-light"
    default:
      return "bg-warm-200 text-warm-700 dark:bg-warm-800 dark:text-warm-300"
  }
})

const dotColor = computed(() => {
  switch (palette.value) {
    case "neutral":
      return "bg-warm-500"
    case "teal":
      return "bg-teal"
    case "amber":
      return "bg-amber"
    case "iolite":
      return "bg-iolite"
    case "rose":
      return "bg-rose"
    case "violet":
      return "bg-violet"
    case "cyan":
      return "bg-cyan"
    case "lime":
      return "bg-lime"
    default:
      return "bg-warm-500"
  }
})

const title = computed(() => props.nodeId)
</script>
