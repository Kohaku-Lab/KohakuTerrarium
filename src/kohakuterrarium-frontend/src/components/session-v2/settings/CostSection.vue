<template>
  <SectionShell :title="t('set.cost.title')" :hint="t('set.cost.hint')">
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <div v-for="card in cards" :key="card.key" class="rounded-lg border kt-v2-line px-4 py-3">
        <div class="text-[11px] uppercase tracking-wider text-warm-500">{{ t(card.label) }}</div>
        <div class="mt-1 font-mono text-xl" :class="card.cls">{{ card.value }}</div>
      </div>
    </div>
    <table class="w-full text-sm" data-test="v2-settings-cost">
      <thead>
        <tr class="text-left text-[11px] uppercase tracking-wider text-warm-400">
          <th class="py-2 pr-4 font-medium">{{ t("set.cost.conversation") }}</th>
          <th class="py-2 pr-4 font-medium">{{ t("set.cost.model") }}</th>
          <th class="py-2 pr-4 font-medium text-right">{{ t("set.cost.in") }}</th>
          <th class="py-2 pr-4 font-medium text-right">{{ t("set.cost.out") }}</th>
          <th class="py-2 pr-4 font-medium text-right">{{ t("set.cost.cached") }}</th>
          <th class="py-2 font-medium text-right">{{ t("set.cost.estimate") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key" class="border-t kt-v2-line">
          <td class="py-2 pr-4 text-warm-800 dark:text-warm-100">{{ row.label }}</td>
          <td class="py-2 pr-4 font-mono text-xs text-warm-500">{{ row.model || "—" }}</td>
          <td class="py-2 pr-4 font-mono text-xs text-right">{{ formatTokens(row.prompt) }}</td>
          <td class="py-2 pr-4 font-mono text-xs text-right">{{ formatTokens(row.completion) }}</td>
          <td class="py-2 pr-4 font-mono text-xs text-right text-aquamarine">{{ formatTokens(row.cached) }}</td>
          <td class="py-2 font-mono text-xs text-right" :class="row.cost != null ? 'text-iolite dark:text-iolite-light' : 'text-warm-400'">{{ row.cost != null ? `$${row.cost.toFixed(4)}` : "—" }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="6" class="py-6 text-center text-warm-400">{{ t("set.cost.empty") }}</td>
        </tr>
      </tbody>
    </table>
    <p class="text-xs text-warm-400">{{ t("set.cost.note") }}</p>
  </SectionShell>
</template>

<script setup>
import { computed } from "vue"

import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { estimateCost, formatTokens } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"

/** Token usage per conversation and in total, with a cost estimate (conversations only) where the model's price is known. */
const t = useV2T()
const session = useSessionV2()
const chat = session.chat

function modelOf(key) {
  const live = chat.modelByTab?.[key]
  return live?.llmName || live?.model || chat.sessionInfo?.llmName || chat.sessionInfo?.model || ""
}

// Prices key on the provider's model id; the preset label is the fallback.
function pricedModel(key) {
  const live = chat.modelByTab?.[key]
  return live?.model || chat.sessionInfo?.model || modelOf(key)
}

const rows = computed(() =>
  Object.entries(chat.tokenUsage || {})
    .filter(([, u]) => (u?.total || u?.prompt || u?.completion) > 0)
    .map(([key, u]) => ({
      key,
      label: tabLabel(key, chat._rootSourceName),
      model: modelOf(key),
      prompt: u.prompt || 0,
      completion: u.completion || 0,
      cached: u.cached || 0,
      cost: estimateCost(pricedModel(key), u),
    })),
)

const totals = computed(() => chat.sessionTokenTotals || { prompt: 0, completion: 0, cached: 0 })
const totalCost = computed(() => {
  const priced = rows.value.filter((r) => r.cost != null)
  return priced.length ? priced.reduce((sum, r) => sum + r.cost, 0) : null
})

const cards = computed(() => [
  { key: "in", label: "set.cost.in", value: formatTokens(totals.value.prompt), cls: "text-warm-800 dark:text-warm-100" },
  { key: "out", label: "set.cost.out", value: formatTokens(totals.value.completion), cls: "text-warm-800 dark:text-warm-100" },
  { key: "cached", label: "set.cost.cached", value: formatTokens(totals.value.cached), cls: "text-aquamarine" },
  { key: "cost", label: "set.cost.estimate", value: totalCost.value != null ? `$${totalCost.value.toFixed(4)}` : "—", cls: totalCost.value != null ? "text-iolite dark:text-iolite-light" : "text-warm-400" },
])
</script>
