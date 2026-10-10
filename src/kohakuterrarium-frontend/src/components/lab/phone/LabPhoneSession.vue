<template>
  <PhonePage :title="digest?.title || session.name" :back-label="t('lab.title')" :scroll="false" test-id="lab-phone-page" @back="graphOpen ? (graphOpen = false) : $emit('back')">
    <template v-if="graphOpen">
      <div class="relative flex-1 min-h-0"><GraphSurface :key="session.id" store-key="lab-phone" :session-id="session.id" lock-session /></div>
    </template>
    <template v-else>
      <div class="flex-1 min-h-0 overflow-y-auto overscroll-contain">
        <section class="kt-v2-line px-4 py-3 border-b" data-test="lab-phone-summary">
          <p v-if="digest?.summary" class="m-0 text-[15px] leading-6 text-warm-800 dark:text-warm-100 whitespace-pre-wrap break-words">{{ digest.summary }}</p>
          <p v-else class="m-0 text-sm text-warm-400">{{ t("lab.session.noSummary") }}</p>
        </section>
        <template v-if="session.savedName">
          <button type="button" class="kt-v2-line w-full min-h-11 flex items-center gap-2 px-4 border-0 border-b border-solid text-left text-sm text-warm-700 dark:text-warm-200 active:bg-warm-200/60 dark:active:bg-warm-800" :aria-expanded="quoteOpen" data-test="lab-phone-quote-toggle" @click="quoteOpen = !quoteOpen"><span :class="quoteOpen ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" class="text-warm-400" />{{ quoteOpen ? t("lab.history.hideQuote") : t("lab.history.showQuote") }}</button>
          <div v-if="quoteOpen" class="kt-v2-line px-4 py-3 border-b" data-test="lab-phone-quote"><HistoryQuote :session-key="session.savedName" :limit="3" /></div>
        </template>
        <div class="kt-lab-phone-graph kt-v2-line h-56 border-b"><LabMiniGraph :session="session" :width="390" :height="224" :active="active" /></div>
        <h3 class="h-10 flex items-center gap-1.5 px-4 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
          {{ t("lab.phone.creatures") }}<span class="font-mono font-normal tracking-normal text-warm-400">{{ session.size }}</span>
        </h3>
        <div v-for="c in session.creatures" :key="c.id" class="kt-v2-line min-h-14 flex items-center gap-3 px-4 py-2 border-b" :data-test="`lab-phone-creature-${c.id}`">
          <span class="w-5 shrink-0 flex justify-center">
            <span v-if="c.status === 'busy'" class="i-carbon-circle-dash animate-spin text-aquamarine" />
            <span v-else class="w-2 h-2 rounded-full" :class="statusStyle(c.status).dot" />
          </span>
          <span class="flex-1 min-w-0">
            <span class="flex items-center gap-1.5 min-w-0">
              <span class="truncate text-[15px] text-warm-800 dark:text-warm-100">{{ c.name }}</span>
              <span v-if="c.privileged" class="i-carbon-security text-iolite/80 shrink-0" :title="t('graph.group.privileged')" />
            </span>
            <span class="block truncate text-xs font-mono text-warm-500">{{ c.model || c.configName || "—" }}</span>
          </span>
          <span class="shrink-0 text-xs" :class="statusStyle(c.status).text">{{ t(`graph.status.${c.status}`) }}</span>
        </div>
      </div>
      <footer class="kt-v2-chrome kt-v2-edge shrink-0 border-t flex gap-2 px-4 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <button type="button" class="kt-v2-edge kt-v2-panel h-11 px-4 rounded-lg border text-sm text-warm-700 dark:text-warm-200 flex items-center gap-1.5" data-test="lab-phone-graph" @click="graphOpen = true"><span class="i-carbon-network-3" />{{ t("lab.graphAll") }}</button>
        <button type="button" class="flex-1 h-11 rounded-lg bg-iolite text-white text-sm font-medium flex items-center justify-center gap-1.5 active:bg-iolite-shadow" data-test="lab-phone-open" @click="$emit('open', session)"><span class="i-carbon-chat" />{{ t("lab.session.open") }}</button>
      </footer>
    </template>
  </PhonePage>
</template>

<script setup>
import { ref } from "vue"

import { statusStyle } from "@/components/graph/graphTheme"
import GraphSurface from "@/components/graph/GraphSurface.vue"
import LabMiniGraph from "@/components/lab/shared/LabMiniGraph.vue"
import PhonePage from "@/components/session-v2/phone/PhonePage.vue"
import HistoryQuote from "@/components/shell/history/HistoryQuote.vue"
import { useI18n } from "@/utils/i18n"

/**
 * One running session on a phone, full screen with a back bar: what it is
 * about (its summary), its latest messages quoted on demand, its graph in
 * miniature and its creatures (status, model); Graph opens the live graph
 * in the same page, Open chat its chat tab.
 */
defineProps({
  session: { type: Object, required: true },
  digest: { type: Object, default: null },
  active: { type: Boolean, default: false },
})
defineEmits(["back", "open"])

const { t } = useI18n()
const graphOpen = ref(false)
const quoteOpen = ref(false)
</script>

<style scoped>
.kt-lab-phone-graph {
  background-color: var(--v2-canvas);
  background-image: radial-gradient(rgb(160 154 146 / 0.35) 1px, transparent 1px);
  background-size: 14px 14px;
}
</style>
