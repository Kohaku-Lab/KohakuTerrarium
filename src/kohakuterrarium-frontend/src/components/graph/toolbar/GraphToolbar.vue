<template>
  <div class="flex items-center gap-2 px-2 py-1.5 border-b border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950 flex-wrap">
    <select v-if="!view.lockedSession" id="graph-scope" v-model="scope" class="kt-graph-select max-w-48" :title="t('graph.toolbar.scope')">
      <option value="">{{ t("graph.toolbar.allSessions", { n: view.model.sessions.length }) }}</option>
      <option v-for="s in view.model.sessions" :key="s.id" :value="s.id">{{ s.name }} · {{ s.creatureIds.length }}</option>
    </select>

    <div class="flex rounded-md border border-warm-200 dark:border-warm-700 overflow-hidden" role="radiogroup" :aria-label="t('graph.toolbar.view')">
      <button v-for="m in viewModes" :key="m.id" role="radio" :aria-checked="view.view === m.id" class="h-7 px-2 text-xs flex items-center gap-1" :class="view.view === m.id ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :title="m.hint" @click="view.view = m.id">
        <span :class="m.icon" /><span v-if="!compact">{{ m.label }}</span>
      </button>
    </div>

    <select id="graph-group-by" v-model="view.groupBy" class="kt-graph-select" :title="t('graph.toolbar.groupBy')">
      <option v-for="g in groupModes" :key="g" :value="g">{{ t(`graph.groupBy.${g}`) }}</option>
    </select>

    <div v-if="view.view === 'network'" class="flex items-center gap-1">
      <button v-for="layer in layerDefs" :key="layer.id" class="kt-graph-chip" :class="view.layers[layer.id] ? layer.on : ''" :aria-pressed="view.layers[layer.id]" @click="view.toggleLayer(layer.id)">{{ layer.label }}</button>
      <button v-if="view.layers.channels" class="kt-graph-chip" :title="t('graph.toolbar.channelModeHint')" @click="view.channelMode = view.channelMode === 'node' ? 'inline' : 'node'">
        {{ view.channelMode === "node" ? t("graph.toolbar.channelHubs") : t("graph.toolbar.channelInline") }}
      </button>
      <select id="graph-edge-style" v-model="view.edgeStyle" class="kt-graph-select" :title="t('graph.toolbar.edgeStyle')">
        <option v-for="s in edgeStyles" :key="s" :value="s">{{ t(`graph.edgeStyle.${s}`) }}</option>
      </select>
    </div>

    <select v-if="view.view === 'flow'" id="graph-privileged-links" v-model="view.privilegedLinks" class="kt-graph-select" :title="t('graph.toolbar.privilegedLinks')">
      <option v-for="m in privilegedLinkModes" :key="m" :value="m">{{ t(`graph.privilegedLinks.${m}`) }}</option>
    </select>

    <div class="relative flex-1 min-w-32 max-w-64">
      <span class="i-carbon-search absolute left-2 top-1/2 -translate-y-1/2 text-xs text-warm-400" />
      <input id="graph-search" ref="searchEl" v-model="view.search" class="kt-graph-select w-full pl-6" :placeholder="t('graph.toolbar.search')" @keydown.escape="view.search = ''" />
    </div>

    <button class="kt-graph-chip" :class="view.focusMode ? 'kt-graph-chip--on' : ''" :aria-pressed="view.focusMode" :title="t('graph.toolbar.focusHint')" @click="view.focusMode = !view.focusMode"><span class="i-carbon-center-circle" /> {{ t("graph.toolbar.focus") }}</button>

    <span class="flex-1" />

    <select v-if="!view.lockedSession" id="graph-sample" v-model="sampleValue" class="kt-graph-select" :title="t('graph.toolbar.sampleHint')">
      <option value="">{{ t("graph.toolbar.live") }}</option>
      <option v-for="p in samplePresets" :key="p" :value="p">{{ t("graph.toolbar.sample", { name: p }) }}</option>
    </select>
    <button class="kt-graph-action" :title="t('graph.toolbar.relayout')" @click="view.relayout()"><span class="i-carbon-renew" /></button>
    <button class="kt-graph-action" :title="t('graph.toolbar.fit')" @click="$emit('fit')"><span class="i-carbon-fit-to-screen" /></button>
    <button class="kt-graph-action" :class="view.minimap ? 'kt-graph-chip--on' : ''" :title="t('graph.toolbar.minimap')" @click="view.minimap = !view.minimap"><span class="i-carbon-map" /></button>
    <div class="relative">
      <button class="kt-graph-action kt-graph-action--primary" :aria-expanded="addOpen" @click="addOpen = !addOpen"><span class="i-carbon-add" />{{ compact ? "" : t("graph.toolbar.add") }}</button>
      <div v-if="addOpen" class="absolute right-0 top-full mt-1 z-30 min-w-44 rounded-lg border border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 shadow-lg py-1" @mouseleave="addOpen = false">
        <button v-for="item in addItems" :key="item.id" class="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-40" :disabled="item.disabled" @click="pickAdd(item.id)"><span :class="item.icon" />{{ item.label }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { EDGE_STYLES, GROUP_MODES, useGraphViewStore } from "@/stores/graph/view"
import { SAMPLE_PRESETS } from "@/utils/graph/data/sample"
import { PRIVILEGED_LINK_MODES } from "@/utils/graph/views/flow"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  storeKey: { type: String, required: true },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(["fit", "quick-add", "new-session"])

const { t } = useI18n()
const view = useGraphViewStore(props.storeKey)
const addOpen = ref(false)
const searchEl = ref(null)

const groupModes = GROUP_MODES
const edgeStyles = EDGE_STYLES
const privilegedLinkModes = PRIVILEGED_LINK_MODES
const samplePresets = Object.keys(SAMPLE_PRESETS)

const scope = computed({
  get: () => view.effectiveSessionId || "",
  set: (id) => view.setSession(id || null),
})

const sampleValue = computed({
  get: () => view.sample || "",
  set: (value) => {
    view.sample = value || null
    view.setSession(null)
  },
})

const viewModes = computed(() => [
  { id: "network", label: t("graph.view.network"), icon: "i-carbon-network-3", hint: t("graph.view.networkHint") },
  { id: "flow", label: t("graph.view.flow"), icon: "i-carbon-flow", hint: t("graph.view.flowHint") },
  { id: "tiers", label: t("graph.view.tiers"), icon: "i-carbon-tree-view-alt", hint: t("graph.view.tiersHint") },
  { id: "bus", label: t("graph.view.bus"), icon: "i-carbon-table-split", hint: t("graph.view.busHint") },
])

const layerDefs = computed(() => [
  { id: "channels", label: t("graph.layer.channels"), on: "kt-graph-chip--aqua" },
  { id: "wires", label: t("graph.layer.wires"), on: "kt-graph-chip--sapphire" },
  { id: "lineage", label: t("graph.layer.lineage"), on: "kt-graph-chip--on" },
  { id: "control", label: t("graph.layer.control"), on: "kt-graph-chip--on" },
])

const addItems = computed(() => [
  { id: "creature", label: t("graph.action.addCreature"), icon: "i-carbon-bot", disabled: !view.model.sessions.length || view.isSample },
  { id: "channel", label: t("graph.action.addChannel"), icon: "i-carbon-flow-stream", disabled: !view.model.sessions.length || view.isSample },
  { id: "session", label: t("graph.action.newSession"), icon: "i-carbon-network-4", disabled: view.lockedSession },
])

function pickAdd(id) {
  addOpen.value = false
  if (id === "session") emit("new-session")
  else emit("quick-add", id)
}

defineExpose({ focusSearch: () => searchEl.value?.focus() })
</script>
