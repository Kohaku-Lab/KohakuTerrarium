<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-950">
    <!-- Optional main header (title / actions) -->
    <div v-if="$slots.header" class="shrink-0 px-4 py-2 border-b border-warm-200 dark:border-warm-800 bg-warm-100/40 dark:bg-warm-900/40">
      <slot name="header" />
    </div>
    <!-- Scrollable body -->
    <div :class="['flex-1 min-h-0', scroll ? 'overflow-y-auto' : 'overflow-hidden']">
      <slot />
    </div>
    <!-- Optional main footer -->
    <div v-if="$slots.footer" class="shrink-0 border-t border-warm-200 dark:border-warm-800">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  scroll: { type: Boolean, default: true },
})
</script>
