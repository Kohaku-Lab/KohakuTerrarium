<template>
  <div class="kt-lab-tile kt-v2-card !rounded-lg flex flex-col overflow-hidden text-left cursor-pointer select-none" :class="session.status === 'error' ? 'kt-lab-tile--error' : ''" role="button" tabindex="0" :aria-label="digest?.summary || name" :data-test="`lab-tile-${session.id}`" @click="$emit('focus', session.id)" @keydown.enter.self="$emit('focus', session.id)" @contextmenu.prevent.stop="menuAt($event.clientX, $event.clientY)">
    <header class="shrink-0 flex items-center gap-2 pl-3 pr-1.5" :style="{ height: `${TILE.head}px` }">
      <span v-if="session.counts.busy" class="i-carbon-circle-dash animate-spin text-aquamarine shrink-0" />
      <span v-else class="w-2 h-2 rounded-full shrink-0" :class="statusStyle(session.status).dot" />
      <span class="flex-1 min-w-0 truncate text-[12px] text-warm-500 dark:text-warm-400" :title="name" data-test="lab-tile-name">{{ name }}</span>
      <span v-if="session.counts.error" class="shrink-0 text-[10px] px-1.5 rounded bg-coral/10 text-coral">{{ t("lab.session.errors", { n: session.counts.error }) }}</span>
      <span class="shrink-0 flex items-center gap-1.5 text-[11px] font-mono text-warm-500" :title="countsTitle" data-test="lab-tile-counts">
        <span class="flex items-center gap-0.5"><span class="i-carbon-bot text-xs" />{{ session.size }}</span>
        <span v-if="session.counts.busy" class="flex items-center gap-0.5 text-aquamarine-shadow dark:text-aquamarine-light"><span class="w-1.5 h-1.5 rounded-full bg-aquamarine" />{{ session.counts.busy }}</span>
        <span v-if="session.hosts.length > 1" class="flex items-center gap-0.5"><span class="i-carbon-bare-metal-server text-xs" />{{ session.hosts.length }}</span>
      </span>
      <button type="button" class="shrink-0 h-7 px-2 rounded-md flex items-center gap-1 text-xs text-iolite dark:text-iolite-light hover:bg-iolite/10" :title="t('lab.session.open')" data-test="lab-tile-open" @click.stop="$emit('open', session)"><span class="i-carbon-chat" />{{ t("lab.session.chat") }}</button>
      <button type="button" class="shrink-0 w-7 h-7 rounded-md flex items-center justify-center text-warm-400 hover:text-warm-700 dark:hover:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800" :title="t('lab.session.more')" data-test="lab-tile-more" @click.stop="menuFrom($event)"><span class="i-carbon-overflow-menu-horizontal" /></button>
    </header>
    <div class="shrink-0 flex items-start gap-1 pl-1.5 pr-3 min-w-0" :style="{ height: `${level === 'compact' ? TILE.line : TILE.summary}px` }" data-test="lab-tile-summary">
      <button v-if="session.savedName && level !== 'compact'" type="button" class="shrink-0 w-5 h-[18px] flex items-center justify-center rounded text-warm-400 hover:text-iolite hover:bg-iolite/10" :aria-expanded="open" :title="open ? t('lab.history.hideQuote') : t('lab.history.showQuote')" data-test="lab-tile-expand" @click.stop="open = !open"><span :class="open ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" /></button>
      <span v-else class="shrink-0 w-5" />
      <p v-if="digest?.summary" class="m-0 min-w-0 text-[13px] leading-[18px] text-warm-800 dark:text-warm-100 break-words" :class="level === 'compact' ? 'truncate' : 'line-clamp-2'" :title="digest.summary" data-test="lab-tile-text">{{ digest.summary }}</p>
      <p v-else class="m-0 text-[12px] leading-[18px] text-warm-400">{{ t("lab.session.noSummary") }}</p>
    </div>
    <div v-if="open && level !== 'compact'" class="kt-v2-line shrink-0 border-t px-3 py-2.5 cursor-auto" data-test="lab-tile-quote" @click.stop>
      <HistoryQuote :session-key="session.savedName" :limit="2" />
    </div>
    <div v-if="level === 'full'" class="kt-lab-tile-graph kt-v2-line shrink-0 border-t" :style="{ height: `${TILE.graph}px` }">
      <LabMiniGraph :session="session" :width="380" :height="TILE.graph" :active="active" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { statusStyle } from "@/components/graph/graphTheme"
import { TILE } from "@/components/lab/model/labSessions"
import LabMiniGraph from "@/components/lab/shared/LabMiniGraph.vue"
import HistoryQuote from "@/components/shell/history/HistoryQuote.vue"
import { useI18n } from "@/utils/i18n"

/**
 * One running session: its name, status and counts in the head; what it is
 * about (its saved summary) under it, with a chevron that quotes its latest
 * messages; its graph in miniature. `level` "brief" drops the graph,
 * "compact" keeps one summary line. Click or Enter looks inside; Chat opens
 * its chat tab; ⋯ or right-click opens its menu.
 */
const props = defineProps({
  session: { type: Object, required: true },
  active: { type: Boolean, default: false },
  digest: { type: Object, default: null },
  level: { type: String, default: "full" },
})
const emit = defineEmits(["focus", "open", "menu"])

const { t } = useI18n()
const open = ref(false)
const name = computed(() => props.digest?.title || props.session.name)
const countsTitle = computed(() => [t("lab.count.creatures", { n: props.session.size }), props.session.counts.busy ? t("lab.count.working", { n: props.session.counts.busy }) : "", props.session.hosts.length > 1 ? t("lab.count.machines", { n: props.session.hosts.length }) : ""].filter(Boolean).join(" · "))

function menuAt(x, y) {
  emit("menu", { id: props.session.id, x, y })
}
function menuFrom(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  menuAt(rect.left, rect.bottom + 4)
}
</script>

<style scoped>
.kt-lab-tile:hover,
.kt-lab-tile:focus-visible {
  border-color: rgb(90 79 207 / 0.45);
  outline: none;
}
.kt-lab-tile--error {
  border-color: rgb(212 107 107 / 0.5);
}
.kt-lab-tile-graph {
  background-color: var(--v2-canvas);
  background-image: radial-gradient(rgb(160 154 146 / 0.35) 1px, transparent 1px);
  background-size: 14px 14px;
}
</style>
