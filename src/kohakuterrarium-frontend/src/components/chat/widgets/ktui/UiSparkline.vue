<template>
  <!-- A small trend line for kt.ui stat ``trend``. -->
  <svg v-if="points" class="ktui-sparkline" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" role="img" :aria-label="`trend: ${values.join(', ')}`" data-test="ktui-sparkline">
    <polyline :points="points" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
  </svg>
</template>

<script setup>
import { computed } from "vue"

const W = 80
const H = 20
const PAD = 2

const props = defineProps({
  data: { type: Array, default: () => [] },
})

const values = computed(() => props.data.filter((v) => typeof v === "number" && Number.isFinite(v)))
const points = computed(() => {
  const vals = values.value
  if (vals.length < 2) return ""
  const low = Math.min(...vals)
  const span = Math.max(...vals) - low || 1
  return vals.map((v, i) => `${PAD + (i / (vals.length - 1)) * (W - 2 * PAD)},${H - PAD - ((v - low) / span) * (H - 2 * PAD)}`).join(" ")
})
</script>
