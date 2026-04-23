<template>
  <div class="inline-flex items-center rounded-md border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950 overflow-hidden">
    <button v-for="opt in options" :key="opt.value" :class="['px-2.5 py-1 text-xs font-medium transition-colors', modelValue === opt.value ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-900']" :title="opt.hint" :disabled="disabled" @click="$emit('update:modelValue', opt.value)">
      <div v-if="opt.icon" :class="[opt.icon, 'inline-block mr-1 text-sm align-[-2px]']" />
      {{ opt.label }}
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: String, default: "simple" },
  disabled: { type: Boolean, default: false },
})

defineEmits(["update:modelValue"])

const options = computed(() => [
  {
    value: "simple",
    label: t("studio.module.mode.simple"),
    icon: "i-carbon-form",
    hint: t("studio.module.mode.simpleHint"),
  },
  {
    value: "raw",
    label: t("studio.module.mode.raw"),
    icon: "i-carbon-code",
    hint: t("studio.module.mode.rawHint"),
  },
])
</script>
