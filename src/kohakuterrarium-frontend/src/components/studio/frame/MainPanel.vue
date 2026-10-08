<template>
  <div class="h-full flex flex-col">
    <div v-if="$slots.header" class="kt-v2-line shrink-0 px-4 py-2 border-b">
      <slot name="header" />
    </div>
    <div :class="['flex-1 min-h-0', scroll ? 'overflow-y-auto' : 'overflow-hidden']">
      <slot />
    </div>
    <div v-if="$slots.footer" class="kt-v2-line shrink-0 border-t">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  scroll: { type: Boolean, default: true },
})
</script>
