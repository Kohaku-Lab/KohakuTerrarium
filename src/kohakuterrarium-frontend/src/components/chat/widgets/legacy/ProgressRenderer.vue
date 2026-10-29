<template>
  <div class="ui-event-card ui-event-progress">
    <div class="ui-event-header">
      <span :class="spec.complete ? 'i-carbon-checkmark text-aquamarine' : 'i-carbon-time text-iolite'" class="text-sm" />
      <span class="text-xs font-medium" :class="spec.complete ? 'text-aquamarine' : 'text-iolite'">
        {{ spec.label || "Progress" }}
      </span>
      <span v-if="pct != null" class="text-[10px] text-warm-400 ml-auto">{{ pct }}%</span>
    </div>
    <el-progress v-if="!spec.indeterminate" :percentage="pct ?? 0" :status="spec.complete ? 'success' : ''" :show-text="false" :stroke-width="6" />
    <div v-else class="text-xs text-warm-400 italic">working…</div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { ElProgress } from "element-plus"

import { progressPercent } from "./legacyShared.js"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => props.widget.spec || {})
const pct = computed(() => progressPercent(spec.value))
</script>
