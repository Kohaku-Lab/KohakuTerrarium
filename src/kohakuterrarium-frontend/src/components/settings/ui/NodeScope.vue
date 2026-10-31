<template>
  <div class="flex items-center gap-2">
    <SitePicker :model-value="modelValue" :label="t('settings.providers.targetNode')" @update:model-value="$emit('update:modelValue', $event)" />
    <span v-if="modelValue && modelValue !== '_host'" class="text-xs text-amber-shadow dark:text-amber-light">{{ t("settings.providers.targetNodeHint") }}</span>
  </div>
</template>

<script setup>
import SitePicker from "@/components/cluster/SitePicker.vue"
import { useI18n } from "@/utils/i18n"

/** The node a node-scoped section edits; the picker hides itself on a single-node install. */
defineProps({ modelValue: { type: String, default: "_host" } })
defineEmits(["update:modelValue"])

const { t } = useI18n()
</script>
