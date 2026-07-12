<template>
  <div v-if="anyShown" class="inline-flex items-center gap-1 flex-wrap">
    <component :is="clickable ? 'button' : 'span'" v-for="b in badges" :key="b.key" :type="clickable ? 'button' : undefined" class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" :class="[b.chip, clickable ? 'cursor-pointer hover:brightness-110' : '']" @click="clickable && $emit('badge-click', b.key)">
      <span :class="b.icon" />
      {{ b.count }} {{ b.label }}
    </component>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { TONE_CHIP } from "@/utils/driveStatus"

const props = defineProps({
  counts: { type: Object, default: () => ({}) },
  clickable: { type: Boolean, default: false },
  /** When true, hide zero-count badges entirely (default). */
  hideZero: { type: Boolean, default: true },
})

defineEmits(["badge-click"])

const badges = computed(() => {
  const c = props.counts || {}
  const all = [
    { key: "active", label: "active", count: c.active || 0, icon: "i-carbon-play-filled-alt", chip: TONE_CHIP.good },
    { key: "blocked", label: "blocked", count: c.blocked || 0, icon: "i-carbon-warning-alt", chip: TONE_CHIP.bad },
    { key: "waiting", label: "waiting", count: c.waiting || 0, icon: "i-carbon-time", chip: TONE_CHIP.info },
    { key: "paused", label: "paused", count: c.paused || 0, icon: "i-carbon-pause-filled", chip: TONE_CHIP.warn },
    { key: "deadLetter", label: "dead-letter", count: c.deadLetter || 0, icon: "i-carbon-warning-square", chip: TONE_CHIP.bad },
  ]
  return props.hideZero ? all.filter((b) => b.count > 0) : all
})

const anyShown = computed(() => badges.value.length > 0)
</script>
