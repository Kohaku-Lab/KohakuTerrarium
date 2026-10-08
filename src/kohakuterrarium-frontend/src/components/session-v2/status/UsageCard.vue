<template>
  <div class="kt-v2-card p-4 flex flex-col gap-2 min-h-0" data-test="status-usage-card">
    <div class="flex items-center gap-2">
      <span class="i-carbon-meter text-amber" />
      <h3 class="text-sm font-semibold text-warm-700 dark:text-warm-200">{{ t("status.usage") }}</h3>
      <span class="text-[11px] text-warm-400">{{ t("status.sessionScope") }}</span>
    </div>
    <div class="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
      <div class="flex justify-between gap-2">
        <span class="text-warm-400">{{ t("status.promptIn") }}</span
        ><span class="font-mono text-warm-700 dark:text-warm-300">{{ formatTokens(totals.prompt) }}</span>
      </div>
      <div class="flex justify-between gap-2">
        <span class="text-warm-400">{{ t("status.completion") }}</span
        ><span class="font-mono text-warm-700 dark:text-warm-300">{{ formatTokens(totals.completion) }}</span>
      </div>
      <div class="flex justify-between gap-2">
        <span class="text-warm-400">{{ t("status.cached") }}</span
        ><span class="font-mono text-aquamarine">{{ formatTokens(totals.cached) }}</span>
      </div>
      <div class="flex justify-between gap-2">
        <span class="text-warm-400">{{ t("status.total") }}</span
        ><span class="font-mono text-warm-700 dark:text-warm-300">{{ formatTokens(totalTokens) }}</span>
      </div>
    </div>
    <div v-if="contexts.length" class="kt-v2-line mt-1 pt-2 border-t flex flex-col gap-2">
      <span class="text-[11px] text-warm-400">{{ t("status.contextPerAgent") }}</span>
      <div class="flex flex-col gap-2 max-h-28 overflow-y-auto pr-1">
        <ContextBar v-for="row in contexts" :key="row.name" :row="row" :active="row.name === activeContext?.name" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { formatTokens } from "@/components/session-v2/model/status/statusModel"
import { useUsage } from "@/components/session-v2/model/status/useUsage"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import ContextBar from "@/components/session-v2/status/ContextBar.vue"

/** Session-wide token totals, then each agent's own context fill. */
const ctx = useSessionV2()
const t = useV2T()
const { totals, totalTokens, contexts, activeContext } = useUsage(ctx)
</script>
