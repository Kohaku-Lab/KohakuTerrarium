<template>
  <div class="image-viewer h-full w-full overflow-auto bg-warm-100 dark:bg-warm-950 flex items-center justify-center p-3">
    <img v-if="src" :src="src" :alt="alt || 'artifact'" class="max-w-full max-h-full object-contain rounded shadow" />
    <div v-else class="text-warm-400 text-xs italic">No image data.</div>
  </div>
</template>

<script setup>
defineProps({
  src: { type: String, default: "" },
  alt: { type: String, default: "" },
})
</script>
