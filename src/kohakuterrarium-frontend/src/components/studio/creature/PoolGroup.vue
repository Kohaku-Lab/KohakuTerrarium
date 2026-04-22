<template>
  <div class="border-b border-warm-200/60 dark:border-warm-800/60 last:border-b-0">
    <button class="w-full px-3 py-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" @click="$emit('toggle')">
      <div :class="[expanded ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right', 'text-xs shrink-0']" />
      <span class="flex-1 text-left">{{ title }}</span>
      <span class="text-[10px] text-warm-400 dark:text-warm-500 tabular-nums">
        {{ count }}
      </span>
    </button>
    <div v-if="expanded">
      <slot />
      <div v-if="!count && emptyHint" class="px-5 py-2 text-[11px] text-warm-400 italic">
        {{ emptyHint }}
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  count: { type: Number, default: 0 },
  emptyHint: { type: String, default: "" },
  expanded: { type: Boolean, default: true },
})

defineEmits(["toggle"])
</script>
