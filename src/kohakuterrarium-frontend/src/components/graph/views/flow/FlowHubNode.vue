<template>
  <div class="kt-graph-hub relative h-full w-full flex items-center justify-center gap-1 px-2 text-[11px] font-semibold transition-opacity" :class="data.dimmed ? 'opacity-25' : ''" :title="t('graph.flow.hubHint')">
    <Handle id="in" type="target" :position="Position.Top" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <Handle id="out" type="source" :position="Position.Bottom" class="kt-graph-port kt-graph-port--hidden" :connectable="false" />
    <span class="i-carbon-security shrink-0" />
    <span class="truncate">{{ t("graph.flow.hub", { n: data.count }) }}</span>
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
