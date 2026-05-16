<template>
  <div v-if="cluster.showPickers" class="flex items-center gap-2 text-xs">
    <label class="text-warm-500 dark:text-warm-400 shrink-0">{{ label }}</label>
    <select :value="modelValue" class="px-2 py-1 rounded border border-warm-300 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 text-warm-700 dark:text-warm-200 focus:outline-none focus:border-iolite" @change="$emit('update:modelValue', $event.target.value)">
      <option v-for="site in cluster.sites" :key="site.nodeId" :value="site.nodeId">
        {{ site.isHost ? t("cluster.site.host") : site.nodeId }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

defineProps({
  /** Selected node_id; falls back to "_host". */
  modelValue: { type: String, default: "_host" },
  label: { type: String, default: "" },
})
defineEmits(["update:modelValue"])

const cluster = useClusterStore()
const { t } = useI18n()
</script>
