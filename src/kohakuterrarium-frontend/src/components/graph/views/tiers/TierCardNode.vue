<template>
  <div class="kt-graph-creature group relative h-full w-full rounded-xl border bg-white dark:bg-warm-900 flex flex-col gap-1.5 px-3 py-2 transition-opacity" :class="[frameClass, data.dimmed ? 'opacity-25' : '']">
    <Handle id="in" type="target" :position="Position.Top" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <Handle id="out" type="source" :position="Position.Bottom" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <div class="flex items-center gap-1.5 min-w-0">
      <span class="w-2 h-2 rounded-full shrink-0" :class="status.dot" />
      <span class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ c.name }}</span>
      <span v-if="c.privileged" class="text-[10px] px-1.5 rounded-full bg-iolite/12 text-iolite shrink-0 flex items-center gap-0.5"><span class="i-carbon-security" />{{ t("graph.tiers.privileged") }}</span>
      <span class="flex-1" />
      <SiteChip v-if="data.showHost" :node-id="c.hostId" always-show class="shrink-0" />
      <button class="nodrag kt-graph-more" :title="t('graph.menu.more')" @click.stop="openMenu($event, id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
    </div>
    <div class="text-[11px] truncate -mt-1">
      <span :class="status.text">{{ t(`graph.status.${c.status}`) }}</span
      ><span class="text-warm-400"> · {{ c.model || c.configName }}</span>
    </div>
    <div v-for="row in rows" :key="row.key" class="flex items-center gap-1 min-w-0 text-[10px]">
      <span class="w-14 shrink-0 text-warm-400 uppercase tracking-wide">{{ row.label }}</span>
      <button v-for="chip in row.chips.slice(0, 3)" :key="chip.key" class="nodrag kt-graph-chiplet" :class="chip.cls" :title="chip.title" @click.stop="select(chip)">{{ chip.text }}</button>
      <span v-if="row.chips.length > 3" class="text-warm-400 shrink-0">+{{ row.chips.length - 3 }}</span>
      <span v-if="!row.chips.length" class="text-warm-300 dark:text-warm-600">—</span>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from "vue"
import { Handle, Position } from "@vue-flow/core"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { statusStyle } from "@/components/graph/graphTheme"
import { channelNodeId } from "@/utils/graph/data/model"
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
  if (props.selected) return "border-iolite ring-2 ring-iolite/40 shadow-md"
  if (props.data.lit) return "border-iolite/60"
  if (c.value.privileged) return "border-iolite/40 shadow-sm"
  return "border-warm-200 dark:border-warm-700 shadow-sm"
})

const rows = computed(() => {
  const ch = (name, cls) => ({ key: `${cls}:${name}`, text: name, cls, title: name, kind: "channel", id: channelNodeId(c.value.sessionId, name) })
  // A privileged node's channel access is total, so listing it would say nothing.
  const channelRows = c.value.privileged
    ? []
    : [
        { key: "listen", label: t("graph.tiers.listens"), chips: c.value.listen.map((n) => ch(n, "kt-graph-chiplet--listen")) },
        { key: "send", label: t("graph.tiers.sends"), chips: c.value.send.map((n) => ch(n, "kt-graph-chiplet--send")) },
      ]
  return [
    ...channelRows,
    {
      key: "wire",
      label: t("graph.tiers.wires"),
      chips: props.data.wiresOut.map((w) => ({ key: w.id, text: `→ ${props.data.nameOf(w.target)}`, cls: "kt-graph-chiplet--wire", title: w.prompt || "", kind: "edge", id: w.id })),
    },
  ]
})

function select(chip) {
  props.data.onSelect?.(chip.kind, chip.id)
}
</script>
