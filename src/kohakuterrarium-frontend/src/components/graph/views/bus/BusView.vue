<template>
  <div class="kt-bus h-full w-full overflow-auto" tabindex="0" @keydown="onKeydown" @contextmenu.self.prevent>
    <div v-if="!bus.columns.length" class="h-full flex items-center justify-center text-sm text-warm-500">{{ t("graph.empty.title") }}</div>
    <div v-else class="kt-bus-grid relative" :style="{ width: `${HEAD + bus.columns.length * COL + 24}px` }">
      <div v-if="bus.bands.length" class="kt-bus-sticky-top flex" :style="{ paddingLeft: `${HEAD}px` }">
        <div v-for="band in bus.bands" :key="band.id" class="kt-bus-band-head" :style="{ width: `${band.span * COL}px` }">
          <span :class="[groupIcon(band.kind), band.kind === 'control' ? 'text-iolite' : '']" />
          <span class="truncate">{{ band.group ? groupTitle(band.group, t) : band.label }}</span>
        </div>
      </div>

      <div class="kt-bus-sticky-top kt-bus-colheads flex" :style="{ top: bus.bands.length ? '28px' : '0' }">
        <div class="kt-bus-corner shrink-0" :style="{ width: `${HEAD}px` }">
          <span class="text-[10px] uppercase tracking-wider text-warm-500">{{ t("graph.bus.creatures") }}</span>
        </div>
        <div v-for="col in bus.columns" :key="col.id" class="shrink-0 p-1" :style="{ width: `${COL}px` }">
          <div class="kt-bus-colcard group" :class="[isSelected(col.id) ? 'kt-bus-colcard--selected' : '', dim(col.id)]" role="button" tabindex="0" @click="select('creature', col.id)" @dblclick="openChat('creature', col.id)" @contextmenu.prevent="menu($event, { kind: 'creature', item: col.creature }, col.id)">
            <div class="flex items-center gap-1 min-w-0">
              <span class="w-2 h-2 rounded-full shrink-0" :class="statusStyle(col.creature.status).dot" />
              <span class="text-xs font-semibold truncate">{{ col.creature.name }}</span>
              <span v-if="col.creature.privileged" class="i-carbon-security text-[10px] text-iolite shrink-0" />
              <span class="flex-1" />
              <button class="kt-graph-more" :title="t('graph.menu.more')" @click.stop="menu($event, { kind: 'creature', item: col.creature }, col.id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
            </div>
            <div class="text-[10px] text-warm-500 truncate font-mono">{{ col.creature.model || col.creature.configName }}</div>
          </div>
        </div>
      </div>

      <div class="kt-bus-section" :style="{ width: `${HEAD}px` }">{{ t("graph.bus.channels") }}</div>
      <div v-for="row in bus.channelRows" :key="row.id" class="kt-bus-row relative flex" :class="[isSelected(row.id) ? 'kt-bus-row--selected' : '', dim(row.id)]">
        <div class="kt-bus-rowhead shrink-0" :style="{ width: `${HEAD}px` }">
          <div class="kt-graph-channel group h-7 rounded-full flex items-center gap-1.5 pl-2.5 pr-1 cursor-pointer min-w-0" :class="isSelected(row.id) ? 'kt-graph-channel--selected' : ''" role="button" tabindex="0" @click="select('channel', row.id)" @dblclick="openChat('channel', row.id)" @contextmenu.prevent="menu($event, { kind: 'channel', item: row.channel }, row.id)">
            <span class="i-carbon-flow-stream text-sm shrink-0" />
            <span class="font-mono text-xs font-semibold truncate">{{ row.channel.name }}</span>
            <span class="flex-1" />
            <button class="kt-graph-more kt-graph-more--on-fill" :title="t('graph.menu.more')" @click.stop="menu($event, { kind: 'channel', item: row.channel }, row.id)"><span class="i-carbon-overflow-menu-horizontal" /></button>
          </div>
        </div>
        <div v-if="row.span" class="kt-bus-rail kt-bus-rail--channel" :class="pulsing(row.id) ? 'kt-bus-rail-pulse' : ''" :style="railStyle(row.span)" />
        <button v-for="col in bus.columns" :key="col.id" class="kt-bus-cell shrink-0" :class="col.control ? 'kt-bus-cell--control' : ''" :style="{ width: `${COL}px` }" :title="cellTitle(row, col)" :disabled="view.isSample" @click="cycle(row, col)" @contextmenu.prevent="cellMenu($event, row, col)">
          <span class="kt-bus-tap" :class="`kt-bus-tap--${row.cells.get(col.id)?.mode || 'none'}`" />
        </button>
      </div>

      <template v-if="bus.wireRows.length">
        <div class="kt-bus-section kt-bus-section--wire" :style="{ width: `${HEAD}px` }">{{ t("graph.bus.wires") }}</div>
        <div v-for="row in bus.wireRows" :key="row.id" class="kt-bus-row relative flex" :class="[isSelected(row.id) ? 'kt-bus-row--selected' : '', dim(row.id)]">
          <div class="kt-bus-rowhead shrink-0" :style="{ width: `${HEAD}px` }">
            <div class="kt-bus-wirechip group" :class="isSelected(row.id) ? 'kt-bus-wirechip--selected' : ''" role="button" tabindex="0" @click="select('edge', row.id)" @contextmenu.prevent="menu($event, { kind: 'edge', item: row.edge }, row.id)">
              <span class="kt-bus-diamond shrink-0" />
              <span class="truncate">{{ nameAt(row.from) }} → {{ nameAt(row.to) }}</span>
              <span v-if="row.edge.withContent === false" class="text-[9px] opacity-70 shrink-0">{{ t("graph.edge.ping") }}</span>
            </div>
          </div>
          <div class="kt-bus-rail kt-bus-rail--wire" :class="row.edge.withContent === false ? 'kt-bus-rail--ping' : ''" :style="railStyle(row.span)" />
          <div v-for="(col, i) in bus.columns" :key="col.id" class="kt-bus-cell shrink-0" :style="{ width: `${COL}px` }">
            <span v-if="i === row.from" class="kt-bus-diamond kt-bus-diamond--source" />
            <span v-else-if="i === row.to" class="kt-bus-arrowhead" :class="row.to < row.from ? 'kt-bus-arrowhead--left' : ''" />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { groupIcon, groupTitle, statusStyle } from "@/components/graph/graphTheme"
import { useGraphLiveStore } from "@/stores/graph/live"
import { busModel, nextMembership } from "@/utils/graph/layout/place/bus"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
})
const emit = defineEmits(["open-chat", "request-delete", "menu"])

const HEAD = 208
const COL = 112

const { t } = useI18n()
const live = useGraphLiveStore()
const bus = computed(() => busModel(props.view.projection))

function isSelected(id) {
  return props.view.selection?.id === id
}

function dim(id) {
  return props.view.projection.dimmed.has(id) ? "opacity-25" : ""
}

function select(kind, id) {
  props.view.select(kind, id)
}

function openChat(kind, id) {
  props.view.select(kind, id)
  emit("open-chat")
}

function menu(event, target, id) {
  props.view.select(target.kind, id)
  emit("menu", { x: event.clientX, y: event.clientY, target })
}

function nameAt(index) {
  return bus.value.columns[index]?.creature.name || "?"
}

function railStyle(span) {
  return { left: `${HEAD + span[0] * COL + COL / 2}px`, width: `${(span[1] - span[0]) * COL}px` }
}

function pulsing(id) {
  const at = live.pulses[id]
  return at && Date.now() - at < 900
}

function cellTitle(row, col) {
  const mode = row.cells.get(col.id)?.mode || "none"
  const now = mode === "none" ? t("graph.bus.notMember") : t(`graph.edge.mode.${mode}`)
  return `${col.creature.name} · ${row.channel.name}: ${now}\n${t("graph.bus.clickToCycle")}`
}

function cycle(row, col) {
  const from = row.cells.get(col.id)?.mode || "none"
  props.actions.setMembership(col.creature, row.channel.name, from, nextMembership(from))
}

function cellMenu(event, row, col) {
  const cell = row.cells.get(col.id)
  if (!cell) return menu(event, { kind: "creature", item: col.creature }, col.id)
  const edge = props.view.index.get(cell.edgeId)?.item
  if (edge) menu(event, { kind: "edge", item: edge }, cell.edgeId)
}

function onKeydown(e) {
  if (e.key === "Escape") props.view.select(null)
  if ((e.key === "Delete" || e.key === "Backspace") && props.view.selection) emit("request-delete")
  if (e.key === "Enter" && props.view.selection) emit("open-chat")
}
</script>
