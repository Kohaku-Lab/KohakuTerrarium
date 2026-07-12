<template>
  <div class="flex flex-col gap-1">
    <button type="button" class="text-[11px] text-warm-500 hover:text-iolite inline-flex items-center gap-1 self-start" @click="collapsed = !collapsed">
      <span :class="collapsed ? 'i-carbon-chevron-right' : 'i-carbon-chevron-down'" />
      {{ label }}
      <span v-if="error" class="text-coral">· invalid</span>
    </button>
    <textarea v-if="!collapsed" class="input-field !text-[11px] font-mono" :class="{ 'border-coral': error }" rows="3" :value="modelValue" @input="onInput" />
  </div>
</template>

<script setup>
import { ref, watch } from "vue"

const props = defineProps({
  modelValue: { type: String, default: "{}" },
  label: { type: String, default: "" },
  collapsedByDefault: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "error"])

const collapsed = ref(props.collapsedByDefault)
const error = ref("")

watch(
  () => props.modelValue,
  (text) => validate(text),
  { immediate: true },
)

function onInput(e) {
  const text = e.target.value
  emit("update:modelValue", text)
  validate(text)
}

function validate(text) {
  try {
    if (text != null && text.trim() !== "") JSON.parse(text)
    error.value = ""
  } catch {
    error.value = `${props.label}: invalid JSON`
  }
  emit("error", error.value)
}
</script>
