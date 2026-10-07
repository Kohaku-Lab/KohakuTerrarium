<template>
  <div class="kt-graph-creature group relative h-full w-full rounded-lg border bg-white dark:bg-warm-900 flex overflow-hidden transition-opacity" :class="[frameClass, data.dimmed ? 'opacity-25' : '']">
    <Handle id="in" type="target" :position="Position.Left" class="kt-graph-port kt-graph-port--in" />
    <Handle id="send" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--send" :style="{ top: '32%' }" :title="t('graph.legend.sendPort')" />
    <Handle id="wire" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--wire" :style="{ top: '70%' }" :title="t('graph.legend.wirePort')" />
    <span class="w-1.5 shrink-0" :class="status.dot" />
    <div class="flex-1 min-w-0 flex flex-col justify-center px-2.5">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
        <span class="flex-1" />
        <SiteChip v-if="data.showHost" :node-id="c.hostId" always-show class="shrink-0" />
        <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
      </div>
      <div class="text-[11px] truncate">
        <span :class="status.text">{{ t(`graph.status.${c.status}`) }}</span
        ><span class="text-warm-400"> · {{ c.model || c.configName }}</span>
      </div>
      <div v-if="data.rooms?.length" class="flex items-center gap-1 min-w-0 mt-0.5 text-[10px]" :title="t('graph.flow.roomHint')">
        <span class="i-carbon-chat shrink-0 text-aquamarine-shadow dark:text-aquamarine-light" />
        <span v-for="r in data.rooms" :key="r" class="kt-graph-chiplet kt-graph-chiplet--listen">{{ r }}</span>
      </div>
      <div v-if="data.inlets?.length || data.outlets?.length || data.wireChips?.length" class="flex items-center gap-1 min-w-0 mt-0.5 text-[10px]" :title="t('graph.flow.privilegedChipHint')">
        <span class="i-carbon-security shrink-0 text-iolite" />
        <span v-for="r in data.inlets" :key="`in:${r}`" class="kt-graph-chiplet kt-graph-chiplet--control kt-graph-chiplet--full">↓ {{ r }}</span>
        <span v-for="r in data.outlets" :key="`out:${r}`" class="kt-graph-chiplet kt-graph-chiplet--control kt-graph-chiplet--full">↑ {{ r }}</span>
        <span v-for="w in data.wireChips" :key="w.id" class="kt-graph-chiplet kt-graph-chiplet--wire kt-graph-chiplet--full" :class="w.ping ? 'border-dashed' : ''" :title="w.ping ? t('graph.legend.ping') : t('graph.legend.wire')">{{ w.entering ? "←" : "→" }} {{ w.name }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position } from "@vue-flow/core"

import SiteChip from "@/components/cluster/SiteChip.vue"
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
  return "border-warm-200 dark:border-warm-700"
})
</script>
