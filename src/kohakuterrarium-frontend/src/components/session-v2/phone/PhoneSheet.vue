<template>
  <Teleport to="body">
    <div class="kt-v2 fixed inset-0 z-[2000] flex flex-col justify-end" role="dialog" aria-modal="true" :aria-label="title" :data-test="testId">
      <div class="absolute inset-0 bg-black/45" data-test="phone-sheet-backdrop" @click="$emit('close')" />
      <section class="kt-v2-float relative w-full rounded-t-2xl shadow-2xl flex flex-col min-h-0" :class="full ? 'h-[92dvh]' : 'max-h-[85dvh]'">
        <div class="shrink-0 flex justify-center pt-2"><span class="w-10 h-1 rounded-full bg-warm-300 dark:bg-warm-600" /></div>
        <header class="kt-v2-line shrink-0 min-h-12 flex items-center gap-2 pl-4 pr-1 border-b">
          <slot name="title">
            <h2 class="flex-1 min-w-0 truncate text-[15px] font-semibold text-warm-800 dark:text-warm-100">{{ title }}</h2>
          </slot>
          <slot name="actions" />
          <button type="button" class="w-11 h-11 shrink-0 flex items-center justify-center rounded-full text-warm-500 active:bg-warm-200/60 dark:active:bg-warm-800" :aria-label="closeLabel" data-test="phone-sheet-close" @click="$emit('close')"><span class="i-carbon-close text-lg" /></button>
        </header>
        <div class="flex-1 min-h-0 overflow-y-auto overscroll-contain" :class="bodyClass"><slot /></div>
        <footer v-if="$slots.footer" class="kt-v2-line shrink-0 border-t px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"><slot name="footer" /></footer>
        <div v-else class="shrink-0 h-[env(safe-area-inset-bottom)]" />
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, onMounted } from "vue"

/**
 * A bottom sheet for phones: title bar with close, a scrolling body and an
 * optional sticky footer. `full` gives it nearly the whole screen height.
 * The backdrop, the close button and Esc emit `close`.
 */
defineProps({
  title: { type: String, default: "" },
  closeLabel: { type: String, default: "Close" },
  full: { type: Boolean, default: false },
  bodyClass: { type: String, default: "" },
  testId: { type: String, default: "phone-sheet" },
})
const emit = defineEmits(["close"])

function onKeydown(e) {
  if (e.key !== "Escape") return
  e.stopPropagation()
  emit("close")
}
onMounted(() => window.addEventListener("keydown", onKeydown, true))
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown, true))
</script>
