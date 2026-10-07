<template>
  <div class="kt-graph-creature group relative h-full w-full rounded-xl border bg-white dark:bg-warm-900 transition-[opacity,box-shadow] duration-150" :class="[frameClass, data.dimmed ? 'opacity-25' : '']">
    <Handle id="in" type="target" :position="Position.Left" class="kt-graph-port kt-graph-port--in" />
    <Handle id="send" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--send" :style="{ top: '34%' }" :title="t('graph.legend.sendPort')" />
    <Handle id="wire" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--wire" :style="{ top: '70%' }" :title="t('graph.legend.wirePort')" />

    <div v-if="lod === 'far'" class="h-full flex items-center gap-2 px-3 min-w-0">
      <span class="w-3.5 h-3.5 rounded-full shrink-0" :class="status.dot" />
      <span class="text-2xl font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
    </div>
    <div v-else class="h-full flex flex-col justify-center gap-1 pl-3 pr-4 min-w-0">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="w-2 h-2 rounded-full shrink-0" :class="status.dot" :title="statusLabel" />
        <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
        <span v-if="c.privileged" class="i-carbon-security text-xs text-iolite shrink-0" :title="t('graph.node.privileged')" />
        <span class="flex-1" />
        <SiteChip v-if="data.showHost" :node-id="c.hostId" />
        <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
      </div>
      <div class="flex items-center gap-1.5 min-w-0 text-[11px]">
        <span :class="status.text">{{ statusLabel }}</span>
        <span class="text-warm-300 dark:text-warm-600">·</span>
        <span class="font-mono text-warm-500 truncate">{{ c.model || c.configName || "—" }}</span>
      </div>
      <div v-if="lod === 'near'" class="flex items-center gap-1 min-w-0 text-[10px] font-mono">
        <span v-if="c.subagents.length" class="px-1 rounded bg-taaffeite/12 text-taaffeite" :title="c.subagents.join(', ')">◇ {{ c.subagents.length }} sub-agents</span>
        <span class="px-1 rounded bg-warm-100 dark:bg-warm-800 text-warm-500" :title="t('graph.node.tools')">{{ c.tools }} tools</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position, useVueFlow } from "@vue-flow/core"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { lodForZoom, statusStyle } from "@/components/graph/graphTheme"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const { t } = useI18n()
const { viewport } = useVueFlow()
const openMenu = inject("ktGraphMenu", () => {})

const c = computed(() => props.data.creature)
const lod = computed(() => lodForZoom(viewport.value.zoom))
const status = computed(() => statusStyle(c.value.status))
const statusLabel = computed(() => t(`graph.status.${c.value.status}`))

const frameClass = computed(() => {
  if (props.selected) return "border-iolite ring-2 ring-iolite/40 shadow-md"
  if (props.data.lit) return "border-iolite/60 shadow-md"
  if (c.value.privileged) return "border-iolite/40 shadow-sm"
  return "border-warm-200 dark:border-warm-700 shadow-sm"
})
</script>
