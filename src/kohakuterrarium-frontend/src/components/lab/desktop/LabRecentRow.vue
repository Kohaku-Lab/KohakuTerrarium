<template>
  <div class="kt-v2-line border-t" :data-test="`lab-recent-${row.key}`">
    <div class="group flex items-start gap-1.5 pl-2 pr-3.5 py-2 cursor-pointer" :class="open ? 'bg-warm-200/40 dark:bg-warm-700/30' : 'hover:bg-warm-200/50 dark:hover:bg-warm-700/40'" role="button" tabindex="0" :aria-expanded="open" data-test="lab-recent-toggle" @click="$emit('toggle', row.key)" @keydown.enter="$emit('toggle', row.key)">
      <span class="shrink-0 w-4 h-[18px] flex items-center justify-center text-warm-400" :class="open ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" />
      <div class="min-w-0 flex-1">
        <p class="m-0 text-[13px] leading-[18px] text-warm-800 dark:text-warm-100 break-words" :class="open ? 'whitespace-pre-wrap' : 'line-clamp-2'" :title="open ? '' : headline" data-test="lab-recent-headline">
          {{ headline }}<span v-if="open && row.summary && row.summaryFrom" class="text-[11px] text-warm-400"> · {{ t(`lab.history.summary.${row.summaryFrom}`) }}</span>
        </p>
        <div v-if="title || STATUS_CHIP[row.status]" class="mt-0.5 flex items-center gap-1.5 min-w-0">
          <span v-if="STATUS_CHIP[row.status]" class="shrink-0 text-[10px] px-1.5 rounded flex items-center gap-1" :class="STATUS_CHIP[row.status]" :title="t(`lab.history.status.${row.status}Hint`)" :data-test="`lab-recent-status-${row.status}`"><span class="w-1.5 h-1.5 rounded-full bg-current" />{{ t(`lab.history.status.${row.status}`) }}</span>
          <span v-if="title" class="truncate text-[11px] text-warm-500" data-test="lab-recent-title">{{ title }}</span>
        </div>
      </div>
      <span class="shrink-0 pt-0.5 text-[11px] font-mono text-warm-400" :class="resuming === row.key ? 'hidden' : 'group-hover:hidden'">{{ whenLabel(row.lastActive, t) }}</span>
      <button type="button" class="shrink-0 px-2 py-0.5 rounded border border-iolite/40 bg-iolite/10 text-xs text-iolite dark:text-iolite-light hover:bg-iolite hover:text-white disabled:opacity-50" :class="resuming === row.key ? 'inline-flex' : 'hidden group-hover:inline-flex'" :disabled="!!resuming" data-test="lab-recent-resume" @click.stop="$emit('resume', row)">{{ resuming === row.key ? t("sessions.resuming") : t("common.resume") }}</button>
    </div>
    <div v-if="open" class="pl-7 pr-3.5 pb-3 flex flex-col gap-2.5" data-test="lab-recent-detail">
      <HistoryQuote :session-key="row.key" :limit="3" :reload-key="row.lastActive" />
      <div class="flex items-center gap-1.5">
        <button type="button" class="kt-v2-edge kt-v2-card !rounded-lg h-7 px-2.5 border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5" data-test="lab-recent-view" @click="$emit('view', row)"><span class="i-carbon-view" />{{ t("lab.recentRow.viewHistory") }}</button>
        <button type="button" class="h-7 px-2.5 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow disabled:opacity-50 flex items-center gap-1.5" :disabled="!!resuming" data-test="lab-recent-resume-detail" @click="$emit('resume', row)"><span :class="resuming === row.key ? 'i-carbon-renew animate-spin' : 'i-carbon-play'" />{{ resuming === row.key ? t("sessions.resuming") : t("common.resume") }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { recentHeadline, recentTitle } from "@/components/lab/model/recentRow"
import HistoryQuote from "@/components/shell/history/HistoryQuote.vue"
import { whenLabel } from "@/components/shell/history/historyRows"
import { useI18n } from "@/utils/i18n"

/**
 * One saved session in the Recent panel: what it was about (its summary,
 * else its label), a cut-off marker and the title the user gave it, and
 * when; hover offers Resume. Open, it shows the whole summary and quotes the
 * latest messages, with View history / Resume.
 */
const props = defineProps({
  row: { type: Object, required: true },
  open: { type: Boolean, default: false },
  resuming: { type: String, default: "" },
})
defineEmits(["toggle", "view", "resume"])

const STATUS_CHIP = {
  crashed: "bg-coral/10 text-coral",
  shutdown: "bg-amber/10 text-amber-shadow dark:text-amber-light",
}
const { t } = useI18n()

const headline = computed(() => recentHeadline(props.row))
const title = computed(() => recentTitle(props.row))
</script>
