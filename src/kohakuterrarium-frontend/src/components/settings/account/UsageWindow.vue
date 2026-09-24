<template>
  <div v-if="window" class="flex flex-col gap-1">
    <div class="flex items-center justify-between text-xs text-warm-500">
      <span>{{ label }}</span>
      <span v-if="usedLabel">{{ t("settings.account.used", { value: usedLabel }) }}</span>
      <span v-else>{{ t("settings.account.grok.unknown") }}</span>
    </div>
    <div class="h-2 w-full rounded bg-warm-200 dark:bg-warm-700 overflow-hidden">
      <div v-if="tone" data-usage-bar class="h-full" :data-tone="tone" :class="{ 'bg-iolite': tone === 'purple', 'bg-amber': tone === 'amber', 'bg-coral': tone === 'coral' }" :style="{ width: clampPercent(window.used_percent) + '%' }" />
    </div>
    <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs text-warm-500">
      <slot name="details" />
      <time v-if="formatDateTime(window.resets_at)" :title="formatDateTime(window.resets_at)" :aria-label="t('settings.account.resets', { value: formatDateTime(window.resets_at) })" class="text-xs text-warm-500">
        {{ t("settings.account.resets", { value: formatDateTime(window.resets_at, "compact") }) }}
      </time>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import { barTone, clampPercent, formatDateTime, formatPercentLabel } from "./usageFormat"

const props = defineProps({
  label: { type: String, default: "" },
  window: { type: Object, default: null },
})

const { t } = useI18n()
const usedLabel = computed(() => formatPercentLabel(props.window?.used_percent))
const tone = computed(() => barTone(props.window?.used_percent))
</script>
