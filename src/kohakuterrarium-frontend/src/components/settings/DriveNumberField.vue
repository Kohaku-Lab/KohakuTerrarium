<template>
  <label class="flex items-center justify-between gap-2">
    <span class="text-[11px] text-warm-500 dark:text-warm-400 shrink-0">{{ label }}</span>
    <el-input-number :model-value="modelValue" size="small" controls-position="right" :min="min" :max="max" :step="step" class="!w-28 shrink-0" @update:model-value="onUpdate" />
  </label>
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Number, default: 0 },
  label: { type: String, default: "" },
  min: { type: Number, default: undefined },
  max: { type: Number, default: undefined },
  step: { type: Number, default: 1 },
})

const emit = defineEmits(["update:modelValue"])

function onUpdate(value) {
  // el-input-number yields null when cleared; coerce back to the min (or 0)
  // so the settings dict never carries a null where a number is required.
  if (value == null || Number.isNaN(value)) {
    emit("update:modelValue", props.min ?? 0)
    return
  }
  emit("update:modelValue", value)
}
</script>
