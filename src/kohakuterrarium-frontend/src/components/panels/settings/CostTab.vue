<template>
  <div class="p-4 text-xs">
    <div class="mb-3 text-[10px] text-warm-400 italic">
      Cost shown when a price is known for the active model. Tokens alone are always visible.
    </div>

    <div class="grid grid-cols-2 gap-2 mb-4">
      <div class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2">
        <div class="text-[9px] uppercase tracking-wider text-warm-400">Input tokens</div>
        <div class="font-mono text-lg text-warm-700 dark:text-warm-300">
          {{ formatTokens(totals.prompt) }}
        </div>
      </div>
      <div class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2">
        <div class="text-[9px] uppercase tracking-wider text-warm-400">Output tokens</div>
        <div class="font-mono text-lg text-warm-700 dark:text-warm-300">
          {{ formatTokens(totals.completion) }}
        </div>
      </div>
      <div
        v-if="totals.cached > 0"
        class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2"
      >
        <div class="text-[9px] uppercase tracking-wider text-warm-400">Cached</div>
        <div class="font-mono text-lg text-aquamarine">
          {{ formatTokens(totals.cached) }}
        </div>
      </div>
      <div class="rounded border border-warm-200 dark:border-warm-700 px-3 py-2">
        <div class="text-[9px] uppercase tracking-wider text-warm-400">Estimated cost</div>
        <div class="font-mono text-lg" :class="cost ? 'text-iolite' : 'text-warm-400'">
          {{ cost ? "$" + cost.toFixed(4) : "—" }}
        </div>
      </div>
    </div>

    <div class="text-[10px] text-warm-500">
      Model:
      <span class="font-mono text-warm-700 dark:text-warm-300">{{ model || "default" }}</span>
    </div>
    <div v-if="!cost && model" class="text-[10px] text-warm-400 mt-1">
      No price table entry for this model. Add one in the settings to see cost estimates.
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useChatStore } from "@/stores/chat"

// Simple shipped price table (USD per 1M tokens, in/out).
// Conservative defaults — users can override in later phases.
const DEFAULT_PRICES = {
  "gpt-4o": { in: 2.5, out: 10 },
  "gpt-4o-mini": { in: 0.15, out: 0.6 },
  "claude-opus-4-6": { in: 15, out: 75 },
  "claude-sonnet-4-6": { in: 3, out: 15 },
  "claude-haiku-4-5": { in: 1, out: 5 },
  o1: { in: 15, out: 60 },
  "o1-mini": { in: 3, out: 12 },
}

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const model = computed(() => chat.sessionInfo.model || props.instance?.model || "")

const totals = computed(() => {
  let prompt = 0
  let completion = 0
  let cached = 0
  for (const u of Object.values(chat.tokenUsage || {})) {
    prompt += u.prompt || 0
    completion += u.completion || 0
    cached += u.cached || 0
  }
  return { prompt, completion, cached }
})

const cost = computed(() => {
  const m = model.value
  if (!m) return null
  // Match prefix; e.g. "claude-opus-4-6[1m]" matches "claude-opus-4-6".
  for (const [name, rate] of Object.entries(DEFAULT_PRICES)) {
    if (m.startsWith(name)) {
      const t = totals.value
      return (t.prompt * rate.in + t.completion * rate.out) / 1_000_000
    }
  }
  return null
})

function formatTokens(n) {
  if (!n) return "0"
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k"
  return String(n)
}
</script>
