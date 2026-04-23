<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-800 text-xs text-warm-600 dark:text-warm-300">
      <div class="i-carbon-document-preliminary text-sm" />
      <span class="font-medium">{{ t("studio.module.preview.title") }}</span>
    </div>
    <div class="flex-1 min-h-0 overflow-auto px-3 py-3 flex flex-col items-center justify-center gap-2 text-xs text-warm-500 text-center">
      <div class="i-carbon-view text-2xl opacity-60" />
      <div>{{ t("studio.module.preview.soon") }}</div>
      <div class="opacity-70 max-w-[200px]">
        {{ t("studio.module.preview.soonHint", { kind, name }) }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
})
</script>
