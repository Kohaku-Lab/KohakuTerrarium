<template>
  <section class="max-w-[90%] overflow-hidden rounded-lg border bg-warm-50 dark:bg-warm-800" :class="message.error ? 'border-coral/35 dark:border-coral/40' : 'border-iolite/20 dark:border-iolite/25'" aria-live="polite">
    <header class="flex items-center gap-2 border-b px-3 py-2" :class="message.error ? 'border-coral/20 dark:border-coral/25' : 'border-iolite/15 dark:border-iolite/20'">
      <span class="i-carbon-terminal text-xs" :class="message.error ? 'text-coral' : 'text-iolite dark:text-iolite-light'" />
      <code class="min-w-0 truncate text-xs font-semibold text-warm-700 dark:text-warm-200">{{ message.command }}</code>
      <span v-if="payload?.title" class="ml-auto truncate text-[11px] text-warm-500 dark:text-warm-400">{{ payload.title }}</span>
    </header>

    <div v-if="message.error" class="whitespace-pre-wrap break-words px-3 py-2 text-xs text-coral-shadow dark:text-coral-light">
      {{ message.error }}
    </div>

    <dl v-else-if="payload?.type === 'info_panel'" class="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 px-3 py-2 text-xs">
      <template v-for="field in payload.fields || []" :key="field.key">
        <dt class="text-warm-400">{{ field.key }}</dt>
        <dd class="min-w-0 break-words text-warm-700 dark:text-warm-200">{{ field.value }}</dd>
      </template>
    </dl>

    <ul v-else-if="payload?.type === 'list'" class="max-h-64 overflow-y-auto px-3 py-1.5">
      <li v-for="(item, index) in payload.items || []" :key="`${item.label || 'item'}:${index}`" class="border-b border-warm-200/70 py-1.5 last:border-b-0 dark:border-warm-700/70">
        <div class="break-words text-xs text-warm-700 dark:text-warm-200">{{ item.label }}</div>
        <div v-if="item.description" class="break-words text-[11px] text-warm-500 dark:text-warm-400">{{ item.description }}</div>
      </li>
    </ul>

    <div v-else-if="message.content" class="whitespace-pre-wrap break-words px-3 py-2 text-xs text-warm-700 dark:text-warm-200">
      {{ message.content }}
    </div>

    <div v-else class="px-3 py-2 text-xs text-warm-500 dark:text-warm-400">{{ t("chat.command.completed") }}</div>
  </section>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  message: { type: Object, required: true },
})

const { t } = useI18n()
const payload = computed(() => {
  const data = props.message.data
  return data && typeof data === "object" ? data : null
})
</script>
