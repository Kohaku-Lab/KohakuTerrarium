<template>
  <div class="card p-3">
    <div class="flex items-center gap-2 text-[10px] text-warm-400 mb-1">
      <span>{{ costAvailable ? t("sessionViewer.trace.timeline.cost") : t("sessionViewer.trace.timeline.tokens") }}</span>
      <span v-if="turns.length" class="ml-auto font-mono">turn 1 – {{ turns[turns.length - 1].turn_index }}</span>
    </div>
    <div class="flex items-end gap-px h-12 min-w-0">
      <button v-for="t2 in turns" :key="t2.turn_index" class="flex-1 min-w-[2px] rounded-t" :class="barClass(t2)" :style="{ height: heightFor(t2) }" :title="tooltipFor(t2)" @click="$emit('select-turn', t2.turn_index)" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useTurnRollupStore } from "@/stores/turnRollup"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const rollup = useTurnRollupStore()

const turns = computed(() => rollup.turns)
const costAvailable = computed(() => rollup.costAvailable)
const maxCost = computed(() => Math.max(rollup.maxCost, 1e-9))
const maxTok = computed(() => Math.max(rollup.maxTokenVolume, 1))

defineEmits(["select-turn"])

function heightFor(turn) {
  if (costAvailable.value) {
    const c = Number(turn.cost_usd || 0)
    const pct = (c / maxCost.value) * 100
    return `${Math.max(2, Math.min(100, pct))}%`
  }
  const v = Number(turn.tokens_in || 0) + Number(turn.tokens_out || 0)
  const pct = (v / maxTok.value) * 100
  return `${Math.max(2, Math.min(100, pct))}%`
}

function barClass(turn) {
  // Hot-cold gradient via opacity tiers; coral overlay for error turns.
  if (turn.has_error) return "bg-coral hover:bg-coral/90"
  return "bg-iolite/70 hover:bg-iolite"
}

function tooltipFor(turn) {
  const parts = [`#${turn.turn_index}`]
  if (turn.cost_usd != null) parts.push(`$${Number(turn.cost_usd).toFixed(3)}`)
  const tin = Number(turn.tokens_in || 0)
  const tout = Number(turn.tokens_out || 0)
  if (tin || tout) parts.push(`${tin}/${tout} tok`)
  return parts.join("  ·  ")
}
</script>
