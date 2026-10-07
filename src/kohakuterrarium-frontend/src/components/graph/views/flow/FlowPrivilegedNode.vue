<template>
  <div class="kt-graph-creature group relative h-full w-full rounded-lg border bg-white dark:bg-warm-900 flex overflow-hidden transition-opacity" :class="[frameClass, data.dimmed ? 'opacity-25' : '']" :title="t('graph.flow.privilegedHint')">
    <Handle id="in" type="target" :position="Position.Top" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <Handle id="out" type="source" :position="Position.Bottom" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <span class="w-1.5 shrink-0" :class="status.dot" />
    <div class="flex-1 min-w-0 flex flex-col justify-center px-2.5">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="i-carbon-security text-xs text-iolite shrink-0" />
        <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
        <span class="flex-1" />
        <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
      </div>
      <div class="text-[11px] truncate">
        <span :class="status.text">{{ t(`graph.status.${c.status}`) }}</span
        ><span class="text-warm-400"> · {{ c.model || c.configName }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position } from "@vue-flow/core"

import { statusStyle } from "@/components/graph/graphTheme"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const { t } = useI18n()
const openMenu = inject("ktGraphMenu", () => {})
const c = computed(() => props.data.creature)
const status = computed(() => statusStyle(c.value.status))
const frameClass = computed(() => {
  if (props.selected) return "border-iolite ring-2 ring-iolite/40"
  if (props.data.lit) return "border-iolite/60"
  return "border-iolite/40"
})
</script>
