<template>
  <div class="relative h-full w-full rounded-full border flex items-center justify-center gap-1.5 px-3 text-[13px] font-semibold transition-opacity" :class="[data.kind === 'user' ? 'border-iolite/50 text-iolite bg-iolite/[0.06]' : 'border-warm-300 dark:border-warm-600 text-warm-700 dark:text-warm-200 bg-white dark:bg-warm-900', data.dimmed ? 'opacity-25' : '']">
    <Handle id="in" type="target" :position="Position.Top" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <Handle id="out" type="source" :position="Position.Bottom" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <span :class="data.kind === 'user' ? 'i-carbon-user' : 'i-carbon-network-4'" class="shrink-0" />
    <span class="truncate">{{ data.kind === "user" ? t("graph.tiers.user") : data.label }}</span>
  </div>
</template>

<script setup>
import { Handle, Position } from "@vue-flow/core"

import { useI18n } from "@/utils/i18n"

defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
})

const { t } = useI18n()
</script>
