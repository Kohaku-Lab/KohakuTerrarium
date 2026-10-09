<template>
  <section class="max-w-4xl mx-auto w-full px-8 py-6 max-md:px-3 max-md:py-3 flex flex-col gap-5 max-md:gap-3">
    <header class="flex items-start gap-4 max-md:flex-col max-md:gap-2">
      <div class="flex-1 min-w-0">
        <h2 class="text-lg font-semibold text-warm-800 dark:text-warm-100 max-md:hidden">{{ title }}</h2>
        <p v-if="hint" class="mt-1 text-sm text-warm-500 leading-relaxed">{{ hint }}</p>
      </div>
      <slot name="aside" />
    </header>
    <div v-if="error" class="kt-v2-b rounded-lg border border-coral/30 bg-coral/10 px-3 py-2 text-sm text-coral" role="alert">{{ error }}</div>
    <div v-else-if="status" class="kt-v2-b rounded-lg border border-aquamarine/30 bg-aquamarine/10 px-3 py-2 text-sm text-aquamarine-shadow dark:text-aquamarine-light" role="status">{{ status }}</div>
    <div class="kt-v2-card p-5 max-md:p-3 overflow-x-auto"><slot /></div>
    <footer v-if="$slots.actions" class="kt-v2-canvas kt-v2-line sticky bottom-0 flex items-center gap-2 py-3 border-t"><slot name="actions" /></footer>
  </section>
</template>

<script setup>
/** Frame of one v2 settings section: title, hint, error / status line, body and a save bar. On phones the page bar carries the title. */
defineProps({
  title: { type: String, required: true },
  hint: { type: String, default: "" },
  error: { type: String, default: "" },
  status: { type: String, default: "" },
})
</script>
