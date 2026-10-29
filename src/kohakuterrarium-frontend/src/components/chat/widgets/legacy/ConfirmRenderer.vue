<template>
  <div class="ui-event-card" :class="['accent-' + (spec.accent || 'warning'), { 'ui-event-done': isResolved }]">
    <div class="ui-event-header">
      <span class="i-carbon-warning-alt text-amber text-sm" />
      <span class="text-xs font-medium text-amber">Confirm</span>
      <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
    </div>
    <div v-if="spec.prompt" class="text-sm py-2 px-1 font-medium">{{ spec.prompt }}</div>
    <div v-if="spec.detail" class="text-xs text-warm-500 px-1 mb-2 whitespace-pre-wrap">{{ spec.detail }}</div>
    <div v-if="!isResolved" class="flex gap-2 flex-wrap">
      <el-button v-for="opt in spec.options || []" :key="opt.id" :type="confirmButtonType(opt.style)" :plain="opt.id !== (spec.default || '')" @click="host.send(opt.id, {})">{{ opt.label || opt.id }}</el-button>
    </div>
    <div v-else-if="repliedActionId" class="text-sm text-warm-500 italic px-1">→ {{ repliedActionId }}</div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { ElButton } from "element-plus"

import { confirmButtonType, useResolution } from "./legacyShared.js"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => props.widget.spec || {})
const { isResolved, resolvedLabel, repliedActionId } = useResolution(props)
</script>
