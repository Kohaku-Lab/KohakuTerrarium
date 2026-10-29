<template>
  <div class="ui-event-card" :class="{ 'ui-event-done': isResolved }">
    <div class="ui-event-header">
      <span class="i-carbon-list-checked text-iolite text-sm" />
      <span class="text-xs font-medium text-iolite">{{ spec.multi ? "Pick options" : "Pick one" }}</span>
      <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
    </div>
    <div v-if="spec.prompt" class="text-sm py-2 px-1">{{ spec.prompt }}</div>
    <div v-if="!isResolved" class="flex flex-col gap-2">
      <template v-if="spec.multi">
        <el-checkbox-group v-model="multiSelectedValues">
          <div v-for="opt in spec.options || []" :key="opt.id" class="flex flex-col py-1">
            <el-checkbox :label="opt.id" :value="opt.id">{{ opt.label || opt.id }}</el-checkbox>
            <span v-if="opt.description" class="text-xs text-warm-400 ml-7">{{ opt.description }}</span>
          </div>
        </el-checkbox-group>
      </template>
      <template v-else>
        <el-radio-group v-model="singleSelectedValue">
          <div v-for="opt in spec.options || []" :key="opt.id" class="flex flex-col py-1">
            <el-radio :label="opt.id" :value="opt.id">{{ opt.label || opt.id }}</el-radio>
            <span v-if="opt.description" class="text-xs text-warm-400 ml-6">{{ opt.description }}</span>
          </div>
        </el-radio-group>
      </template>
      <!-- The Submit/Cancel pair wraps below the options at a narrow width
           instead of overflowing the card, like the confirm row. -->
      <div class="flex gap-2 flex-wrap mt-2">
        <el-button type="primary" :disabled="!hasSelection" @click="onSubmit">Submit</el-button>
        <el-button @click="host.send('cancel', {})">Cancel</el-button>
      </div>
    </div>
    <div v-else-if="repliedSelectionLabel" class="text-sm text-warm-500 italic px-1">→ {{ repliedSelectionLabel }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElCheckbox, ElCheckboxGroup, ElRadio, ElRadioGroup } from "element-plus"

import { useResolution } from "./legacyShared.js"

const props = defineProps({
  widget: { type: Object, required: true },
  host: { type: Object, required: true },
})

const spec = computed(() => props.widget.spec || {})
const { isResolved, resolvedLabel, repliedValues } = useResolution(props)
const singleSelectedValue = ref(spec.value.default || "")
const multiSelectedValues = ref(Array.isArray(spec.value.default) ? [...spec.value.default] : [])

const hasSelection = computed(() => (spec.value.multi ? multiSelectedValues.value.length > 0 : Boolean(singleSelectedValue.value)))

const repliedSelectionLabel = computed(() => {
  const sel = repliedValues.value?.selected
  if (Array.isArray(sel)) return sel.join(", ")
  return sel || ""
})

function onSubmit() {
  const selected = spec.value.multi ? multiSelectedValues.value : singleSelectedValue.value
  props.host.send("submit", { selected })
}
</script>
