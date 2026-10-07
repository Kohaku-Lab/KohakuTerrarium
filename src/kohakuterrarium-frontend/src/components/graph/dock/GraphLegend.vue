<template>
  <section class="flex flex-col gap-1.5 pt-2 border-t border-warm-200 dark:border-warm-700">
    <h4 class="kt-graph-section">{{ t("graph.legend.title") }}</h4>
    <div class="grid grid-cols-[1.75rem_1fr] gap-x-2 gap-y-1 items-center text-[11px] text-warm-600 dark:text-warm-300">
      <template v-for="key in keys" :key="key">
        <svg v-if="LINES[key]" width="26" height="10" :data-legend="key">
          <line x1="0" y1="5" x2="22" y2="5" :stroke="LINES[key].color" :stroke-width="LINES[key].width" :stroke-dasharray="LINES[key].dash" :stroke-opacity="LINES[key].opacity" />
        </svg>
        <svg v-else-if="key === 'spawn'" width="26" height="12" :data-legend="key"><path d="M11 0 V5 H3 V12 M11 5 H19 V12" fill="none" :stroke="EDGE_COLOR.authority" stroke-width="1.5" /></svg>
        <span v-else-if="key === 'group'" class="w-6 h-3 rounded border-2 border-dashed border-iolite/50 justify-self-center" :data-legend="key" />
        <span v-else-if="key === 'hub'" class="w-6 h-2.5 rounded-full border border-iolite/60 bg-iolite/10 justify-self-center" :data-legend="key" />
        <span v-else-if="key === 'bundle'" class="kt-graph-bundle-label px-1 rounded text-[9px] font-mono leading-3 justify-self-center" :data-legend="key">ch</span>
        <span v-else-if="key === 'room'" class="kt-graph-chiplet kt-graph-chiplet--listen justify-self-center" :data-legend="key">ch</span>
        <span v-else-if="key === 'privilegedChip'" class="kt-graph-chiplet kt-graph-chiplet--control justify-self-center" :data-legend="key">↑</span>
        <span v-else-if="key === 'wireChip'" class="kt-graph-chiplet kt-graph-chiplet--wire justify-self-center" :data-legend="key">→</span>
        <span v-else-if="key === 'busListen'" class="w-2.5 h-2.5 rounded-full border-2 border-solid border-aquamarine justify-self-center" :data-legend="key" />
        <span v-else-if="key === 'busSend'" class="w-2.5 h-2.5 rounded-full bg-aquamarine justify-self-center" :data-legend="key" />
        <span v-else-if="key === 'sendPort'" class="w-3 h-3 rounded-full border-2 border-solid border-aquamarine justify-self-center" :data-legend="key" />
        <span v-else-if="key === 'wirePort'" class="w-2.5 h-2.5 border-2 border-solid border-sapphire rotate-45 justify-self-center" :data-legend="key" />
        <span>{{ t(`graph.legend.${key}`) }}</span>
      </template>
    </div>
    <div class="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-warm-600 dark:text-warm-300">
      <span v-for="s in statuses" :key="s"
        ><span :class="statusStyle(s).text">{{ statusStyle(s).glyph }}</span> {{ t(`graph.status.${s}`) }}</span
      >
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue"

import { EDGE_COLOR, statusStyle } from "@/components/graph/graphTheme"
import { legendKeys } from "@/utils/graph/views/legend"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
})

const { t } = useI18n()
const statuses = ["busy", "idle", "paused", "stopped", "error"]

const LINES = {
  membership: { color: EDGE_COLOR.channel, width: 2 },
  handoff: { color: EDGE_COLOR.via, width: 2 },
  control: { color: EDGE_COLOR.control, width: 2 },
  wire: { color: EDGE_COLOR.wire, width: 2.5 },
  ping: { color: EDGE_COLOR.wire, width: 2, dash: "5 3" },
  back: { color: EDGE_COLOR.via, width: 2, opacity: 0.35 },
  lineage: { color: EDGE_COLOR.lineage, width: 1.5, dash: "2 4" },
}

const keys = computed(() => legendKeys(props.view.view, props.view.projection, { privilegedLinks: props.view.privilegedLinks }))
</script>
