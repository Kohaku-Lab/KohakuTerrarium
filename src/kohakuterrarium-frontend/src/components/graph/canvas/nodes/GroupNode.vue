<template>
  <div class="kt-graph-backdrop h-full w-full rounded-2xl border-2 border-dashed" :class="frameClass">
    <Handle id="in" type="target" :position="Position.Top" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <Handle id="out" type="source" :position="Position.Bottom" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <div class="kt-graph-backdrop-head flex items-center gap-2 h-9 px-3 min-w-0">
      <span :class="[groupIcon(g.kind), g.kind === 'control' ? 'text-iolite' : 'text-warm-500']" class="text-sm shrink-0" />
      <SiteChip v-if="g.kind === 'host'" :node-id="g.key" always-show />
      <span v-else class="text-sm font-semibold text-warm-700 dark:text-warm-200 truncate shrink-0 max-w-[60%]">{{ groupTitle(g, t) }}</span>
      <span class="flex-1 min-w-0 flex items-center gap-1 text-[11px] font-mono text-warm-500" :title="t('graph.group.counts', { n: g.creatureIds.length, busy: g.busy })">
        <span class="i-carbon-bot text-xs shrink-0" />{{ g.creatureIds.length }}
        <span v-if="g.busy" class="inline-flex items-center gap-0.5 text-aquamarine"><span class="w-1.5 h-1.5 rounded-full bg-aquamarine" />{{ g.busy }}</span>
      </span>
      <button class="nodrag text-warm-500 hover:text-iolite p-0.5 rounded shrink-0" :title="t('graph.group.collapse')" @click.stop="data.onToggleCollapse(g.id)">
        <span class="i-carbon-collapse-categories text-sm" />
      </button>
      <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position } from "@vue-flow/core"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { groupIcon, groupTitle } from "@/components/graph/graphTheme"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const { t } = useI18n()
const openMenu = inject("ktGraphMenu", () => {})
const g = computed(() => props.data.group)
const frameClass = computed(() => {
  if (props.selected) return "border-iolite/70 bg-iolite/5"
  if (g.value.kind === "control") return "border-iolite/35 bg-iolite/[0.04]"
  return "border-warm-300 dark:border-warm-700 bg-warm-100/40 dark:bg-warm-900/30"
})
</script>
