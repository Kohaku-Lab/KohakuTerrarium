<template>
  <section>
    <div class="flex items-baseline justify-between mb-2">
      <h2 class="text-sm font-medium text-warm-700 dark:text-warm-300">
        {{ title }}
      </h2>
      <button v-if="action" class="text-xs text-iolite hover:underline" @click="$emit('action')">
        {{ action }}
      </button>
    </div>
    <slot />
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  action: { type: String, default: "" },
})
defineEmits(["action"])
</script>
