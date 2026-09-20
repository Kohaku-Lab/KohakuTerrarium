<template>
  <div v-if="executionTarget ? cluster.isCluster : cluster.showPickers" class="flex items-center gap-2 text-xs">
    <label class="text-warm-500 dark:text-warm-400 shrink-0">{{ label }}</label>
    <select :value="modelValue" class="px-2 py-1 rounded border border-warm-300 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 text-warm-700 dark:text-warm-200 focus:outline-none focus:border-iolite" @change="$emit('update:modelValue', $event.target.value)">
      <option v-if="executionTarget && !sites.some((site) => site.nodeId === modelValue)" :value="modelValue" disabled>Select a site</option>
      <option v-for="site in sites" :key="site.nodeId" :value="site.nodeId">
        {{ site.isHost ? t("cluster.site.host") : site.nodeId }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { computed, watch } from "vue"
import { useClusterStore } from "@/stores/cluster"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  /** Selected node_id; falls back to "_host". */
  modelValue: { type: String, default: "_host" },
  label: { type: String, default: "" },
  /** Lab execution requires a worker, even when only one is connected. */
  executionTarget: { type: Boolean, default: false },
})
const emit = defineEmits(["update:modelValue"])

const cluster = useClusterStore()
const { t } = useI18n()
const sites = computed(() => (props.executionTarget ? cluster.workerSites : cluster.sites))

watch(
  [() => cluster.isCluster, sites],
  () => {
    if (!props.executionTarget || !cluster.isCluster) return
    if (props.modelValue === "_host") {
      emit("update:modelValue", sites.value[0]?.nodeId || "")
    } else if (props.modelValue && !sites.value.some((site) => site.nodeId === props.modelValue)) {
      // Losing a worker invalidates the choice; never redirect an intended run.
      emit("update:modelValue", "")
    }
  },
  { immediate: true },
)
</script>
