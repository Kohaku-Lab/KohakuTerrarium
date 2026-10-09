<template>
  <div class="kt-v2-canvas h-full flex flex-col min-h-0" :data-test="testId">
    <header class="kt-v2-chrome kt-v2-line shrink-0 h-12 flex items-center gap-1 pr-2 border-b">
      <button type="button" class="w-11 h-11 shrink-0 flex items-center justify-center text-warm-600 dark:text-warm-300 active:bg-warm-200/60 dark:active:bg-warm-800 rounded-full" :aria-label="backLabel" data-test="phone-page-back" @click="$emit('back')"><span class="i-carbon-chevron-left text-xl" /></button>
      <span v-if="icon" :class="icon" class="shrink-0 text-warm-500" />
      <h2 class="flex-1 min-w-0 truncate text-[15px] font-semibold text-warm-800 dark:text-warm-100">{{ title }}</h2>
      <slot name="actions" />
    </header>
    <div class="flex-1 min-h-0" :class="scroll ? 'overflow-y-auto overscroll-contain' : 'flex flex-col overflow-hidden'"><slot /></div>
  </div>
</template>

<script setup>
/** A full-screen phone page pushed over a list: back bar with title and actions, then its body (scrolling unless `scroll` is false). */
defineProps({
  title: { type: String, default: "" },
  icon: { type: String, default: "" },
  backLabel: { type: String, default: "Back" },
  scroll: { type: Boolean, default: true },
  testId: { type: String, default: "phone-page" },
})
defineEmits(["back"])
</script>
