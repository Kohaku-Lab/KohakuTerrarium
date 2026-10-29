<template>
  <!-- Toast notifications are shown by the store; this renderer is the
       inline ``surface=chat`` form. -->
  <div class="ui-event-card" :class="['accent-' + (spec.level || 'info')]">
    <div class="ui-event-header">
      <span :class="notificationIconClass(spec.level)" class="text-sm" />
      <span class="text-xs font-medium">{{ spec.title || spec.level || "info" }}</span>
    </div>
    <div class="text-sm py-1 px-1">{{ spec.text || "" }}</div>
    <div v-if="spec.action && !isResolved" class="mt-2">
      <el-button :type="confirmButtonType(spec.action.style)" size="small" @click="host.send(spec.action.id, {})">
        {{ spec.action.label || spec.action.id }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { ElButton } from "element-plus"

import { confirmButtonType, notificationIconClass, useResolution } from "./legacyShared.js"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => props.widget.spec || {})
const { isResolved } = useResolution(props)
</script>
