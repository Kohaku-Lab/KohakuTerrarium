<template>
  <div class="relative h-full w-full" :class="data.dimmed ? 'opacity-25' : ''">
    <div class="absolute inset-0 translate-x-1.5 translate-y-1.5 rounded-xl border border-warm-200 dark:border-warm-700 bg-warm-100 dark:bg-warm-800" />
    <div class="relative h-full w-full rounded-xl border bg-white dark:bg-warm-900 px-3 py-2 flex flex-col justify-between" :class="selected ? 'border-iolite ring-2 ring-iolite/40' : 'border-warm-300 dark:border-warm-600'">
      <Handle id="in" type="target" :position="Position.Left" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
      <Handle id="out" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
      <div class="flex items-center gap-1.5 min-w-0">
        <span :class="[groupIcon(g.kind), g.kind === 'control' ? 'text-iolite' : 'text-warm-500']" class="text-sm shrink-0" />
        <SiteChip v-if="g.kind === 'host'" :node-id="g.key" always-show />
        <span v-else class="text-sm font-semibold text-warm-800 dark:text-warm-100 truncate">{{ groupTitle(g, t) }}</span>
        <span class="flex-1" />
        <button class="nodrag text-warm-500 hover:text-iolite p-0.5 rounded" :title="t('graph.group.expand')" @click.stop="data.onToggleCollapse(g.id)">
          <span class="i-carbon-expand-categories text-sm" />
        </button>
        <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
      </div>
      <div class="text-[11px] font-mono text-warm-500">{{ t("graph.group.counts", { n: g.creatureIds.length, busy: g.busy }) }}</div>
      <div class="flex gap-0.5 overflow-hidden">
        <span v-for="s in statusDots" :key="s.key" class="w-1.5 h-1.5 rounded-full shrink-0" :class="s.dot" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position } from "@vue-flow/core"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { groupIcon, groupTitle, statusStyle } from "@/components/graph/graphTheme"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const { t } = useI18n()
const openMenu = inject("ktGraphMenu", () => {})
const g = computed(() => props.data.group)
const statusDots = computed(() => (props.data.memberStatuses || []).slice(0, 40).map((status, i) => ({ key: `${i}`, dot: statusStyle(status).dot })))
</script>
