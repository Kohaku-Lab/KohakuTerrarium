<template>
  <div class="flex flex-col gap-1">
    <span class="text-xs font-medium text-warm-600 dark:text-warm-300">{{ label }}</span>
    <div class="flex items-center gap-2">
      <input :value="modelValue" class="kt-v2-edge kt-v2-panel h-8 flex-1 min-w-0 px-3 rounded-lg border font-mono text-xs text-warm-800 dark:text-warm-100 placeholder-warm-400 focus:outline-none focus:border-iolite" :placeholder="placeholder" spellcheck="false" :data-test="`${testId}-input`" @input="$emit('update:modelValue', $event.target.value)" @change="$emit('picked', $event.target.value.trim())" />
      <button type="button" class="kt-v2-edge kt-v2-panel h-8 px-3 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5" :data-test="`${testId}-browse`" @click="pickerOpen = true"><span class="i-carbon-folder" />{{ browseLabel }}</button>
    </div>
    <DirectoryPickerDialog v-model="pickerOpen" :initial-path="modelValue" @pick="onPick" />
  </div>
</template>

<script setup>
import { ref } from "vue"

import DirectoryPickerDialog from "@/components/common/DirectoryPickerDialog.vue"

/**
 * A local config path, typed or picked with the shared folder browser (a
 * creature or recipe is a folder). `picked` fires when a path is chosen or
 * a typed one is committed, not on every keystroke.
 */
defineProps({
  modelValue: { type: String, default: "" },
  label: { type: String, required: true },
  placeholder: { type: String, default: "" },
  browseLabel: { type: String, required: true },
  testId: { type: String, default: "add-path" },
})
const emit = defineEmits(["update:modelValue", "picked"])

const pickerOpen = ref(false)

function onPick(path) {
  emit("update:modelValue", path)
  emit("picked", path)
}
</script>
