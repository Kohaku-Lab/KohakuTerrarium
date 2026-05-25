<template>
  <div class="rounded-lg overflow-hidden min-w-0 border border-sapphire/20 dark:border-sapphire/25">
    <!-- Header: total + per-tool breakdown + status counters -->
    <div role="button" tabindex="0" :aria-expanded="expanded" :aria-label="expanded ? t('chat.toolBatchCollapse') : t('chat.toolBatchExpand')" class="flex items-center gap-2 text-xs px-3 py-1.5 cursor-pointer select-none min-w-0 bg-sapphire/8 dark:bg-sapphire/12" @click="$emit('toggle')" @keydown.enter="$emit('toggle')" @keydown.space.prevent="$emit('toggle')">
      <span :class="statusIcon.class">{{ statusIcon.icon }}</span>
      <span class="font-semibold font-mono shrink-0 text-iolite dark:text-iolite-light">
        {{ t("chat.toolBatchHeader", { count: summary.total }) }}
      </span>
      <span class="text-warm-400 dark:text-warm-500 truncate flex-1 font-mono min-w-0">{{ nameSummary }}</span>
      <span v-if="summary.counts.running > 0" class="shrink-0 px-1.5 py-px rounded text-[10px] font-mono bg-amber/15 text-amber-shadow dark:text-amber-light"> {{ summary.counts.running }} {{ "⚙" }} </span>
      <span v-if="summary.counts.error > 0" class="shrink-0 px-1.5 py-px rounded text-[10px] font-mono bg-coral/15 text-coral-shadow dark:text-coral-light"> {{ summary.counts.error }} {{ "✗" }} </span>
      <span v-if="summary.counts.interrupted > 0" class="shrink-0 px-1.5 py-px rounded text-[10px] font-mono bg-amber/15 text-amber-shadow dark:text-amber-light"> {{ summary.counts.interrupted }} {{ "○" }} </span>
      <span class="i-carbon-chevron-down text-warm-400 transition-transform text-[10px] shrink-0" :class="{ 'rotate-180': expanded }" />
    </div>

    <!-- Expanded list: per-tool ToolCallBlock with its own expand state. -->
    <div v-if="expanded" class="px-2 py-1.5 space-y-1 bg-warm-100 dark:bg-warm-800/80 border-t border-sapphire/15 dark:border-sapphire/20 max-h-72 overflow-y-auto overflow-x-hidden min-w-0">
      <ToolCallBlock v-for="tc in tools" :key="tc.id" :tc="tc" :expanded="!!toolExpanded[tc.id]" @toggle="$emit('tool-toggle', tc.id)" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import ToolCallBlock from "@/components/chat/ToolCallBlock.vue"
import { summarizeBatch } from "@/utils/chatToolGrouping"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  tools: { type: Array, required: true },
  expanded: { type: Boolean, default: false },
  toolExpanded: { type: Object, default: () => ({}) },
})

defineEmits(["toggle", "tool-toggle"])

const summary = computed(() => summarizeBatch(props.tools))

// Compact name list e.g. ``read x3, bash x1, edit x1``.  Truncated to
// the top 4 names; remainder collapses into ``+N more`` so the chip
// never wraps even for very wide batches.
const nameSummary = computed(() => {
  const top = summary.value.names.slice(0, 4)
  const parts = top.map(([name, count]) => (count > 1 ? `${name} ×${count}` : name))
  const rest = summary.value.names.length - top.length
  if (rest > 0) parts.push(`+${rest} more`)
  return parts.join(", ")
})

// Header status icon mirrors ToolCallBlock so the visual language is
// consistent: running > error > interrupted > done.
const statusIcon = computed(() => {
  const c = summary.value.counts
  if (c.running > 0) return { icon: "⚙", class: "text-amber kohaku-pulse" }
  if (c.error > 0) return { icon: "✗", class: "text-coral" }
  if (c.interrupted > 0) return { icon: "○", class: "text-amber" }
  return { icon: "✓", class: "text-sage" }
})
</script>
