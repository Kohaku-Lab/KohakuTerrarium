<template>
  <div class="flex flex-col gap-1" :data-test="`context-bar-${row.name}`">
    <div class="flex items-center gap-1.5 text-xs">
      <span class="truncate" :class="active ? 'font-semibold text-iolite dark:text-iolite-light' : 'text-warm-700 dark:text-warm-200'">{{ row.name }}</span>
      <span v-if="row.privileged" class="i-carbon-security text-[11px] text-iolite/80 shrink-0" />
      <span class="flex-1" />
      <span v-if="row.maxContext" class="font-mono text-[11px] shrink-0" :class="TONE[row.tone]">{{ formatTokens(row.lastPrompt) }} / {{ formatTokens(row.maxContext) }} · {{ row.pct }}%</span>
      <span v-else class="font-mono text-[11px] text-warm-400 shrink-0">{{ formatTokens(row.lastPrompt) }}</span>
    </div>
    <div class="relative h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
      <div class="absolute inset-y-0 left-0 rounded-full" :class="BAR[row.tone]" :style="{ width: `${row.pct}%` }" />
      <div v-if="row.compactPct" class="absolute inset-y-0 w-px bg-amber" :style="{ left: `${row.compactPct}%` }" :title="t('status.compactAt', { pct: row.compactPct })" />
    </div>
  </div>
</template>

<script setup>
import { formatTokens } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** One creature's context fill against its own model window, with the compaction mark. */
defineProps({
  row: { type: Object, required: true },
  active: { type: Boolean, default: false },
})

const TONE = { ok: "text-warm-500", warn: "text-amber", bad: "text-coral" }
const BAR = { ok: "bg-aquamarine", warn: "bg-amber", bad: "bg-coral" }

const t = useV2T()
</script>
