<template>
  <div class="flex flex-col gap-2">
    <p class="text-[11px] text-warm-500 leading-relaxed">
      {{ t("studio.module.optionsSchema.intro") }}
    </p>
    <ParamsTable :params="modelValue" @update:params="$emit('update:modelValue', $event)" />
  </div>
</template>

<script setup>
import ParamsTable from "@/components/studio/module/ParamsTable.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  /** Array of {name, type_hint, default, required, description}. */
  modelValue: { type: Array, default: () => [] },
})

defineEmits(["update:modelValue"])
</script>
