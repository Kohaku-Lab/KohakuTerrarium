<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="$emit('close')">
    <div class="bg-warm-50 dark:bg-warm-950 rounded-lg shadow-xl border border-warm-200 dark:border-warm-700 w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
      <header class="flex items-center justify-between px-4 py-3 border-b border-warm-200 dark:border-warm-700">
        <h3 class="text-sm font-medium text-warm-800 dark:text-warm-200">
          <slot name="title" />
        </h3>
        <button class="i-carbon-close w-5 h-5 text-warm-400 hover:text-warm-700" @click="$emit('close')" />
      </header>
      <div class="flex-1 overflow-y-auto p-4">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="px-4 py-3 border-t border-warm-200 dark:border-warm-700">
        <slot name="footer" />
      </footer>
    </div>
  </div>
</template>

<script setup>
defineEmits(["close"])

import { onMounted, onUnmounted } from "vue"

function onKey(e) {
  if (e.key === "Escape") {
    e.stopPropagation()
    // Bubble through emit
    document.dispatchEvent(new CustomEvent("kt:modal:escape"))
  }
}

onMounted(() => {
  if (typeof document !== "undefined") document.addEventListener("keydown", onKey)
})
onUnmounted(() => {
  if (typeof document !== "undefined") document.removeEventListener("keydown", onKey)
})
</script>
