<template>
  <div class="kt-v2 kt-v2-canvas relative h-full flex flex-col overflow-hidden" data-test="lab-phone">
    <div class="flex-1 min-h-0 overflow-y-auto overscroll-contain">
      <div class="px-3 pt-3 empty:hidden"><LabRestoreBanner @restored="bench.live.refresh()" /></div>
      <template v-if="sessions.length">
        <h2 class="kt-v2-canvas sticky top-0 z-[1] h-10 flex items-center gap-1.5 px-4 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
          {{ t("lab.running") }}<span class="font-mono font-normal tracking-normal text-warm-400">{{ sessions.length }}</span>
        </h2>
        <div v-for="s in sessions" :key="s.id" class="kt-v2-line flex items-center border-b">
          <button type="button" class="flex-1 min-w-0 min-h-14 flex items-center gap-3 pl-4 pr-1 py-2 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`lab-phone-session-${s.id}`" @click="openId = s.id">
            <span class="kt-v2-card !rounded-lg shrink-0 w-10 h-10 flex flex-wrap content-center justify-center gap-[3px] p-1.5" :title="t('lab.count.creatures', { n: s.size })">
              <span v-for="c in s.creatures.slice(0, MAX_DOTS)" :key="c.id" class="w-1.5 h-1.5 rounded-full" :class="statusStyle(c.status).dot" />
            </span>
            <span class="flex-1 min-w-0">
              <span class="text-[15px] leading-5 text-warm-800 dark:text-warm-100 line-clamp-2 break-words" data-test="lab-phone-headline">{{ digests[s.id]?.summary || nameOf(s) }}</span>
              <span class="mt-0.5 flex items-center gap-1.5 min-w-0 text-xs text-warm-500">
                <span v-if="s.counts.busy" class="i-carbon-circle-dash animate-spin text-aquamarine shrink-0" />
                <span v-else class="w-2 h-2 rounded-full shrink-0" :class="statusStyle(s.status).dot" />
                <span class="truncate">{{ digests[s.id]?.summary ? nameOf(s) : t("lab.session.noSummary") }}</span>
              </span>
            </span>
            <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
          </button>
          <button type="button" class="shrink-0 w-11 h-11 mr-2 rounded-full flex items-center justify-center text-iolite dark:text-iolite-light active:bg-iolite/10" :aria-label="t('lab.session.open')" :data-test="`lab-phone-chat-${s.id}`" @click="actions.openChat(s)"><span class="i-carbon-chat text-lg" /></button>
        </div>
      </template>
      <div v-else-if="!bench.loading.value" class="py-12"><LabEmptyHero :starts="recent.starts.value" @new="$emit('new', $event)" /></div>

      <h2 class="kt-v2-canvas sticky top-0 z-[1] h-10 flex items-center gap-1.5 pl-4 pr-1 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
        <span class="flex-1 flex items-center gap-1.5"
          >{{ t("lab.recent") }}<span v-if="recent.total.value" class="font-mono font-normal tracking-normal text-warm-400">{{ recent.total.value }}</span></span
        >
        <button type="button" class="h-9 px-3 rounded-full normal-case tracking-normal text-xs font-normal text-iolite dark:text-iolite-light active:bg-iolite/10" data-test="lab-phone-all" @click="recent.openHistory()">{{ t("lab.history.openAll") }}</button>
      </h2>
      <div v-if="!recent.rows.value.length" class="px-4 pb-4 text-xs text-warm-500">{{ recent.loading.value ? t("sessions.loading") : t("sessions.noSaved") }}</div>
      <div v-for="r in recent.rows.value" :key="r.key" class="kt-v2-line min-h-14 flex items-center gap-3 px-4 py-2 border-b" :data-test="`lab-phone-recent-${r.key}`">
        <button type="button" class="flex-1 min-w-0 text-left" @click="recent.view(r)">
          <span class="text-sm leading-5 text-warm-800 dark:text-warm-100 line-clamp-2 break-words" data-test="lab-phone-recent-headline">{{ recentHeadline(r) }}</span>
          <span class="block truncate text-xs text-warm-500">{{ [recentTitle(r), whenLabel(r.lastActive, t)].filter(Boolean).join(" · ") }}</span>
        </button>
        <button type="button" class="shrink-0 h-9 px-3 rounded-lg border border-iolite/40 bg-iolite/10 text-xs text-iolite dark:text-iolite-light disabled:opacity-50" :disabled="!!recent.resuming.value" data-test="lab-phone-resume" @click="recent.resume(r)">{{ recent.resuming.value === r.key ? t("sessions.resuming") : t("common.resume") }}</button>
      </div>
    </div>
    <footer class="kt-v2-chrome kt-v2-edge shrink-0 border-t px-4 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <button type="button" class="w-full h-11 rounded-lg bg-iolite text-white text-sm font-medium flex items-center justify-center gap-1.5 active:bg-iolite-shadow" data-test="lab-new" @click="$emit('new', null)"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
    </footer>

    <LabPhoneSession v-if="opened" class="absolute inset-0 z-10" :session="opened" :digest="digests[opened.id] || null" :active="bench.active.value.has(opened.id)" @back="openId = null" @open="actions.openChat" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { statusStyle } from "@/components/graph/graphTheme"
import LabRestoreBanner from "@/components/lab/LabRestoreBanner.vue"
import LabPhoneSession from "@/components/lab/phone/LabPhoneSession.vue"
import LabEmptyHero from "@/components/lab/shared/LabEmptyHero.vue"
import { recentHeadline, recentTitle } from "@/components/lab/model/recentRow"
import { whenLabel } from "@/components/shell/history/historyRows"
import { useI18n } from "@/utils/i18n"

/**
 * The lab on a phone: running sessions and recent saved sessions as two
 * groups of rows, each led by what the session is about (its summary)
 * rather than its name, New session as the footer action. A running row opens the
 * session page and its chat button the chat tab; a recent row opens its
 * history, its button resumes it.
 */
const props = defineProps({
  bench: { type: Object, required: true },
  recent: { type: Object, required: true },
  actions: { type: Object, required: true },
})
defineEmits(["new"])

const MAX_DOTS = 16
const { t } = useI18n()
const openId = ref(null)
const sessions = computed(() => props.bench.sessions.value)
const digests = computed(() => props.bench.digests.value)
const opened = computed(() => sessions.value.find((s) => s.id === openId.value) || null)

function nameOf(s) {
  return digests.value[s.id]?.title || s.name
}

watch(opened, (session) => {
  if (openId.value && !session) openId.value = null
})
</script>
