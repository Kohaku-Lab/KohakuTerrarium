<template>
  <div class="flex flex-wrap gap-1.5">
    <span v-if="!options.length" class="text-xs text-warm-500 pt-1">{{ empty }}</span>
    <span v-for="o in options" :key="o" class="kt-v2-b h-7 rounded-full border text-xs flex items-center transition-colors" :class="modelValue.includes(o) ? 'bg-iolite/15 border-iolite/60 text-iolite dark:text-iolite-light font-medium' : 'kt-v2-edge kt-v2-panel text-warm-700 dark:text-warm-200 hover:border-iolite/50'">
      <button type="button" class="h-full px-2.5 flex items-center gap-1 rounded-full" :aria-pressed="modelValue.includes(o)" :data-test="`chip-${o}`" @click="$emit('update:modelValue', toggleIn(modelValue, o))">
        <span v-if="prefix" class="opacity-60">{{ prefix }}</span
        >{{ o }}
        <span v-if="marked.includes(o)" class="text-[10px] px-1 rounded bg-aquamarine/20 text-aquamarine-shadow dark:text-aquamarine-light">{{ markLabel }}</span>
      </button>
      <button v-if="marked.includes(o)" type="button" class="h-full pr-2 -ml-1 flex items-center opacity-70 hover:opacity-100 hover:text-coral" :title="removeLabel" :aria-label="`${removeLabel} ${o}`" @click="$emit('remove', o)"><span class="i-carbon-close text-[11px]" /></button>
    </span>
  </div>
</template>

<script setup>
import { toggleIn } from "@/components/session-v2/model/add/addPlan"

/** A row of toggle chips over `options`; `modelValue` is the selected subset. `marked` chips get a `markLabel` tag and a remove button. */
defineProps({
  options: { type: Array, required: true },
  modelValue: { type: Array, required: true },
  prefix: { type: String, default: "" },
  marked: { type: Array, default: () => [] },
  markLabel: { type: String, default: "" },
  empty: { type: String, default: "" },
  removeLabel: { type: String, default: "" },
})
defineEmits(["update:modelValue", "remove"])
</script>
