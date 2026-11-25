<template>
  <el-tooltip v-if="label" :content="t(originTooltipKey(kind))" placement="top" :show-after="300" popper-class="kt-hint-tooltip">
    <span class="inline-flex items-center px-1.5 py-px rounded border font-mono text-[10px] leading-4 whitespace-nowrap cursor-help" :class="originBadgeClass(kind)" :data-origin-kind="kind" data-test="origin-badge">{{ label }}</span>
  </el-tooltip>
</template>

<script setup>
import { computed } from "vue"

import { originBadgeClass, originKind, originLabel, originTooltipKey } from "./toolOrigin"

import { useI18n } from "@/utils/i18n"

/** Colour-coded label naming where a tool comes from, with a plain-words tooltip for its kind. */
const props = defineProps({
  origin: { type: Object, default: null },
})

const { t } = useI18n()
const kind = computed(() => originKind(props.origin))
const label = computed(() => originLabel(props.origin))
</script>
