<template>
  <div class="ui-event-card" :class="{ 'ui-event-done': isResolved }">
    <div class="ui-event-header">
      <span class="i-carbon-chat text-iolite text-sm" />
      <span class="text-xs font-medium text-iolite">Input requested</span>
      <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
    </div>
    <div v-if="spec.prompt" class="text-sm py-2 px-1">{{ spec.prompt }}</div>
    <!-- At a narrow sidebar width the input + Send + Cancel cannot share a
         row; ``flex-wrap`` drops the buttons below the input instead of
         overflowing the card, and the min-width floor stops the flex-1
         input from collapsing to 0px. -->
    <div v-if="!isResolved" class="flex gap-2 items-stretch flex-wrap">
      <el-input v-model="textValue" :placeholder="spec.placeholder || 'Type your reply…'" :type="spec.multiline ? 'textarea' : 'text'" :autosize="spec.multiline ? { minRows: 3, maxRows: 12 } : false" autofocus class="flex-1 !min-w-[4rem]" @keydown.enter.exact.prevent="onSubmit" />
      <el-button type="primary" :disabled="!textValue.trim()" @click="onSubmit">Send</el-button>
      <el-button @click="host.send('cancel', {})">Cancel</el-button>
    </div>
    <div v-else-if="repliedValues?.text" class="text-sm text-warm-500 italic px-1">→ {{ repliedValues.text }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElInput } from "element-plus"

import { useResolution } from "./legacyShared.js"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => props.widget.spec || {})
const { isResolved, resolvedLabel, repliedValues } = useResolution(props)
const textValue = ref(spec.value.default || "")

function onSubmit() {
  if (!textValue.value.trim()) return
  props.host.send("submit", { text: textValue.value })
}
</script>
