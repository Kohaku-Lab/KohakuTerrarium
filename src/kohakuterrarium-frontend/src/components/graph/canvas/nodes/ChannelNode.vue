<template>
  <div class="kt-graph-channel group relative h-full w-full rounded-full flex items-center gap-1.5 pl-3 pr-2 transition-opacity duration-150" :class="[frameClass, data.dimmed ? 'opacity-25' : '', pulsing ? 'kt-graph-pulse-node' : '']" :title="tooltip">
    <Handle id="in" type="target" :position="Position.Left" class="kt-graph-port kt-graph-port--in" />
    <Handle id="out" type="source" :position="Position.Right" class="kt-graph-port kt-graph-port--send" :title="t('graph.channel.dragHint')" />
    <span class="i-carbon-flow-stream shrink-0" :class="lod === 'far' ? 'text-lg' : 'text-sm'" />
    <span class="font-mono font-semibold truncate" :class="lod === 'far' ? 'text-xl' : 'text-xs'">{{ ch.name }}</span>
    <span class="flex-1" />
    <span v-if="lod !== 'far' && ch.messageCount" class="text-[10px] font-mono opacity-80 shrink-0">{{ ch.messageCount }}</span>
    <button v-if="lod !== 'far'" class="nodrag kt-graph-more kt-graph-more--on-fill" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
  </div>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, ref, watch } from "vue"
import { Handle, Position, useVueFlow } from "@vue-flow/core"

import { lodForZoom } from "@/components/graph/graphTheme"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const PULSE_MS = 700

const { t } = useI18n()
const { viewport } = useVueFlow()
const openMenu = inject("ktGraphMenu", () => {})

const ch = computed(() => props.data.channel)
const lod = computed(() => lodForZoom(viewport.value.zoom))

const frameClass = computed(() => {
  if (props.selected) return "kt-graph-channel--selected"
  if (props.data.lit) return "kt-graph-channel--lit"
  return ""
})

const tooltip = computed(() => {
  const last = props.data.lastMessage
  const head = `${ch.value.name} · ${t("graph.channel.members", { senders: ch.value.senders.length, listeners: ch.value.listeners.length })}`
  return last ? `${head}\n${last.sender}: ${last.preview}` : head
})

const pulsing = ref(false)
let timer = null
watch(
  () => props.data.pulseAt,
  (at) => {
    if (!at || Date.now() - at > PULSE_MS) return
    pulsing.value = false
    requestAnimationFrame(() => {
      pulsing.value = true
      clearTimeout(timer)
      timer = setTimeout(() => (pulsing.value = false), PULSE_MS)
    })
  },
)
onBeforeUnmount(() => clearTimeout(timer))
</script>
