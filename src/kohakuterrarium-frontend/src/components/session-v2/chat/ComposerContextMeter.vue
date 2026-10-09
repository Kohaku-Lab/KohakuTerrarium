<template>
  <button v-if="row && row.maxContext" type="button" class="kt-v2-ctx flex items-center gap-1.5 h-7 px-1.5 rounded-md text-[11px] font-mono" :class="TONE[row.tone]" :title="title" :aria-label="title" data-test="v2-composer-context" @click="session.openWidget('usage')">
    <svg viewBox="0 0 16 16" class="w-3.5 h-3.5 -rotate-90 shrink-0" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-opacity="0.2" stroke-width="2.5" />
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" :stroke-dasharray="`${(row.pct / 100) * CIRC} ${CIRC}`" />
    </svg>
    <span class="kt-v2-ctx__figures">{{ formatTokens(row.lastPrompt) }}/{{ formatTokens(row.maxContext) }}</span>
  </button>
</template>

<script setup>
import { computed } from "vue"

import { formatTokens } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useUsage } from "@/components/session-v2/model/status/useUsage"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/**
 * How full the open conversation's context window is, as a ring and
 * figures; it turns amber, then coral, as it nears compaction. Opens the
 * Usage widget.
 */
const CIRC = 2 * Math.PI * 6
const TONE = {
  ok: "text-warm-400 hover:text-warm-600 dark:hover:text-warm-200",
  warn: "text-amber-shadow dark:text-amber-light",
  bad: "text-coral",
}

const t = useV2T()
const session = useSessionV2()
const { activeContext: row } = useUsage(session)

const title = computed(() => {
  const r = row.value
  if (!r) return ""
  const base = t("composer.context", { used: r.lastPrompt.toLocaleString(), max: r.maxContext.toLocaleString(), pct: r.pct })
  return r.compactPct ? `${base} · ${t("composer.contextCompact", { pct: r.compactPct })}` : base
})
</script>

<style scoped>
.kt-v2-ctx:hover {
  background: color-mix(in srgb, currentColor 10%, transparent);
}
@container (max-width: 30rem) {
  .kt-v2-ctx__figures {
    display: none;
  }
}
</style>
