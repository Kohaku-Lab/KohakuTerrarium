<template>
  <div class="h-full min-h-0 overflow-y-auto" data-test="v2-widget-usage">
    <div v-if="contexts.length" class="kt-v2-line px-3 py-3 border-b flex flex-col gap-2">
      <span class="text-[11px] uppercase tracking-wide text-warm-400">{{ t("status.contextPerAgent") }}</span>
      <ContextBar v-for="row in contexts" :key="row.name" :row="row" :active="row.key === activeContext?.key" />
    </div>

    <div class="px-3 pt-2.5 text-[11px] uppercase tracking-wide text-warm-400">{{ t("status.sessionScope") }}</div>
    <div class="kt-v2-line grid grid-cols-4 border-b text-center">
      <div v-for="cell in cells" :key="cell.key" class="py-2.5 flex flex-col gap-0.5">
        <span class="font-mono text-sm" :class="cell.tone">{{ formatTokens(cell.value) }}</span>
        <span class="text-[10px] text-warm-400">{{ t(cell.key) }}</span>
      </div>
    </div>

    <div v-if="!shares.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.usage.empty") }}</div>
    <div v-for="r in shares" :key="r.key" class="px-3 py-2 flex flex-col gap-1 hover:bg-warm-100 dark:hover:bg-warm-800/60">
      <div class="flex items-center gap-2 text-xs">
        <span class="truncate flex-1 text-warm-800 dark:text-warm-100 font-medium">{{ r.label }}</span>
        <span class="font-mono text-warm-500">{{ formatTokens(r.total) }}</span>
        <span class="font-mono text-[10px] text-warm-400 w-9 text-right">{{ r.pct }}%</span>
      </div>
      <div class="h-1 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
        <div class="h-full rounded-full" :class="r.sub ? 'bg-taaffeite/60' : 'bg-iolite/60'" :style="{ width: `${r.pct}%` }" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { formatTokens, percentOf } from "@/components/session-v2/model/status/statusModel"
import { useUsage } from "@/components/session-v2/model/status/useUsage"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import ContextBar from "@/components/session-v2/status/ContextBar.vue"

/** Token use: each agent's own context fill, then the whole session's totals and each conversation's (and the sub-agents') share. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const { totals, totalTokens, rows, subagentTokens, contexts, activeContext } = useUsage(ctx)

const shares = computed(() => {
  const out = rows.value.map((r) => ({
    key: r.source,
    label: tabLabel(r.source, ctx.chat._rootSourceName),
    total: r.total,
    sub: false,
  }))
  if (subagentTokens.value > 0) out.push({ key: "__subagents", label: t("widget.usage.subagents"), total: subagentTokens.value, sub: true })
  return out.map((r) => ({ ...r, pct: percentOf(r.total, totalTokens.value) }))
})

const cells = computed(() => [
  { key: "status.promptIn", value: totals.value.prompt, tone: "text-warm-700 dark:text-warm-200" },
  { key: "status.completion", value: totals.value.completion, tone: "text-warm-700 dark:text-warm-200" },
  { key: "status.cached", value: totals.value.cached, tone: "text-aquamarine" },
  { key: "status.total", value: totalTokens.value, tone: "text-iolite dark:text-iolite-light" },
])
</script>
