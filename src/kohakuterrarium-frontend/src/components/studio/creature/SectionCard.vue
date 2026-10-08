<template>
  <section :class="['kt-v2-card', dense ? 'p-3' : 'p-4']">
    <header v-if="title || $slots.actions" class="flex items-center gap-2 mb-3">
      <div v-if="icon" :class="[icon, 'text-sm text-iolite dark:text-iolite-light']" />
      <h3 class="text-[13px] font-semibold text-warm-800 dark:text-warm-100 flex-1">
        {{ title }}
      </h3>
      <slot name="actions" />
    </header>
    <slot />
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: "" },
  icon: { type: String, default: "" },
  dense: { type: Boolean, default: false },
})
</script>
