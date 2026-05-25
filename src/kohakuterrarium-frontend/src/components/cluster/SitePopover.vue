<template>
  <div v-if="open" class="absolute left-full top-0 ml-1 z-50 w-64 max-w-[calc(100vw-3rem)] rounded-md border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 shadow-lg" @click.stop>
    <div class="px-3 py-2 border-b border-warm-200 dark:border-warm-700 flex items-center justify-between">
      <span class="text-xs font-medium text-warm-700 dark:text-warm-200">
        {{ t("cluster.popover.title") }}
      </span>
      <button class="text-warm-400 hover:text-warm-700 i-carbon-close w-3 h-3" :title="t('cluster.popover.close')" @click="$emit('close')" />
    </div>
    <ul class="max-h-72 overflow-y-auto text-xs">
      <li v-for="site in cluster.sites" :key="site.nodeId" class="px-3 py-2 flex items-center gap-2 border-b border-warm-100 dark:border-warm-800 last:border-b-0">
        <span class="inline-block w-2 h-2 rounded-full" :class="dotClass(site)" />
        <span class="font-medium text-warm-700 dark:text-warm-200 truncate">{{ site.isHost ? t("cluster.site.host") : site.nodeId }}</span>
        <span class="ml-auto flex items-center gap-1 text-[10px] text-warm-400">
          <span v-if="site.creatures !== null">{{ t("cluster.site.creatureCount", { n: site.creatures }) }}</span>
          <span class="uppercase tracking-wider" :class="statusClass(site)">{{ site.status }}</span>
        </span>
      </li>
    </ul>
    <div v-if="cluster.error" class="px-3 py-2 text-[10px] text-rose">
      {{ cluster.error }}
    </div>
  </div>
</template>

<script setup>
import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

defineProps({
  open: { type: Boolean, default: false },
})
defineEmits(["close"])

const cluster = useClusterStore()
const { t } = useI18n()

function dotClass(site) {
  if (site.status === "online") return "bg-emerald"
  if (site.status === "unreachable") return "bg-rose"
  return "bg-warm-400"
}

function statusClass(site) {
  if (site.status === "online") return "text-emerald-dark dark:text-emerald-light"
  if (site.status === "unreachable") return "text-rose"
  return ""
}
</script>
