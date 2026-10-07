<template>
  <div class="h-full w-full flex items-center justify-center px-1.5 rounded text-[10px] leading-4 font-mono whitespace-nowrap kt-graph-edge-label kt-graph-bundle-label transition-opacity" :class="data.dimmed ? 'opacity-25' : ''" :title="t('graph.flow.bundleHint', { channels: data.text })">{{ data.text }}</div>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
})

const { t } = useI18n()
</script>
