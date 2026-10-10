<template>
  <aside class="kt-v2-panel kt-v2-edge relative shrink-0 border-l flex flex-col min-h-0" :style="collapsed ? null : { width: `${width}px` }" :class="collapsed ? 'w-12' : ''" data-test="lab-recent">
    <div v-if="!collapsed" class="kt-v2-grip -left-[5px]" :class="{ 'is-dragging': dragging }" :title="t('lab.recentPanel.resize')" data-test="lab-recent-grip" @pointerdown="startDrag" @dblclick="resetWidth" />
    <template v-if="collapsed">
      <div class="flex flex-col items-center gap-1 py-2">
        <button type="button" class="w-8 h-8 rounded-md flex items-center justify-center text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-200/60 dark:hover:bg-warm-700/50" :title="t('lab.recentPanel.expand')" data-test="lab-recent-collapse" @click="toggle"><span class="i-carbon-side-panel-open" /></button>
        <button type="button" class="w-8 h-8 rounded-md flex flex-col items-center justify-center text-warm-500 hover:text-iolite hover:bg-warm-200/60 dark:hover:bg-warm-700/50" :title="t('lab.recent')" @click="toggle">
          <span class="i-carbon-recently-viewed" /><span v-if="recent.total.value" class="text-[9px] font-mono leading-none mt-0.5">{{ recent.total.value }}</span>
        </button>
      </div>
    </template>
    <template v-else>
      <header class="h-10 shrink-0 flex items-center gap-2 pl-3.5 pr-1.5">
        <h2 class="flex-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
          {{ t("lab.recent") }}<span v-if="recent.total.value" class="font-mono font-normal tracking-normal text-warm-400">{{ recent.total.value }}</span>
        </h2>
        <button type="button" class="text-xs text-iolite dark:text-iolite-light hover:underline" data-test="lab-recent-all" @click="recent.openHistory()">{{ t("lab.history.openAll") }}</button>
        <button type="button" class="w-7 h-7 rounded-md flex items-center justify-center text-warm-400 hover:text-warm-700 dark:hover:text-warm-200 hover:bg-warm-200/60 dark:hover:bg-warm-700/50" :title="t('lab.recentPanel.collapse')" data-test="lab-recent-collapse" @click="toggle"><span class="i-carbon-side-panel-close" /></button>
      </header>
      <div class="flex-1 min-h-0 overflow-y-auto">
        <div v-if="recent.error.value" class="kt-v2-line border-t px-3.5 py-3 text-[11px] text-coral" role="alert">{{ recent.error.value }}</div>
        <div v-else-if="!recent.rows.value.length" class="kt-v2-line border-t px-3.5 py-3 text-[11px] text-warm-500">{{ recent.loading.value ? t("sessions.loading") : t("sessions.noSaved") }}</div>
        <template v-else>
          <LabRecentRow v-for="r in recent.rows.value" :key="r.key" :row="r" :open="openKey === r.key" :resuming="recent.resuming.value" @toggle="openKey = openKey === $event ? null : $event" @view="recent.view" @resume="recent.resume" />
        </template>
      </div>
      <footer class="kt-v2-edge shrink-0 border-t px-3.5 py-2"><LabStatsLine /></footer>
    </template>
  </aside>
</template>

<script setup>
import { ref } from "vue"

import LabRecentRow from "@/components/lab/desktop/LabRecentRow.vue"
import LabStatsLine from "@/components/lab/desktop/LabStatsLine.vue"
import { useResizableWidth } from "@/composables/useResizableWidth"
import { useI18n } from "@/utils/i18n"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The lab's right panel: the latest saved sessions, one open at a time for
 * its details, All history, and the lab's numbers at the foot. Resizable by
 * its grip (double-click resets) or collapsed to an icon strip; both choices
 * are remembered.
 */
defineProps({ recent: { type: Object, required: true } })

const COLLAPSED_KEY = "kt.lab.recent.collapsed"
const { t } = useI18n()
const { width, dragging, startDrag, reset: resetWidth } = useResizableWidth({ key: "kt.lab.recent.width", min: 240, max: 560, initial: 320, edge: "left" })
const collapsed = ref(readLocalPref(COLLAPSED_KEY) === "1")
const openKey = ref(null)

function toggle() {
  collapsed.value = !collapsed.value
  writeLocalPref(COLLAPSED_KEY, collapsed.value ? "1" : "0")
}
</script>
