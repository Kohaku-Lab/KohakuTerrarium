<template>
  <!-- kt.ui progress: a bar or a ring with the share of value / max. -->
  <div class="ktui-progress" :class="[`ktui-progress-${variant}`, `ktui-tone-${tone}`]" data-test="ktui-progress" role="progressbar" :aria-valuenow="value" aria-valuemin="0" :aria-valuemax="max" :aria-label="label || undefined">
    <div v-if="label && variant === 'bar'" class="ktui-progress-head">
      <span>{{ label }}</span
      ><span>{{ percent }}%</span>
    </div>
    <div v-if="variant === 'bar'" class="ktui-progress-track">
      <div class="ktui-progress-fill" :style="{ width: `${percent}%` }" />
    </div>
    <template v-else>
      <svg viewBox="0 0 40 40" class="ktui-ring-svg" aria-hidden="true">
        <circle cx="20" cy="20" :r="R" class="ktui-progress-ring-track" />
        <circle cx="20" cy="20" :r="R" class="ktui-progress-ring-fill" :stroke-dasharray="`${dash} ${CIRC}`" transform="rotate(-90 20 20)" />
        <text x="20" y="20" class="ktui-progress-ring-text" text-anchor="middle" dominant-baseline="central">{{ percent }}%</text>
      </svg>
      <span v-if="label" class="ktui-progress-ring-label">{{ label }}</span>
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue"

const R = 16
const CIRC = 2 * Math.PI * R

const props = defineProps({
  value: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  label: { type: String, default: "" },
  tone: { type: String, default: "info" },
  variant: { type: String, default: "bar" },
})

const share = computed(() => (props.max > 0 && Number.isFinite(props.value) ? Math.min(1, Math.max(0, props.value / props.max)) : 0))
const percent = computed(() => Math.round(share.value * 100))
const dash = computed(() => share.value * CIRC)
</script>
