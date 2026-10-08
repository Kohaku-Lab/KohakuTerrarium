<template>
  <div class="kt-v2-edge flex flex-col min-h-0 rounded-xl border overflow-hidden" :data-test="`add-picker-${kind}`">
    <div class="kt-v2-line shrink-0 flex items-center gap-2 px-3 h-9 border-b">
      <span class="i-carbon-search text-warm-400" />
      <input v-model="query" class="flex-1 min-w-0 bg-transparent border-none text-sm outline-none text-warm-800 dark:text-warm-100 placeholder-warm-400" :placeholder="t('add.filter')" />
      <span v-if="rows.length" class="text-[11px] font-mono text-warm-400">{{ filtered.length }}</span>
    </div>
    <div class="flex-1 min-h-0 overflow-y-auto py-1">
      <div v-if="loading" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("add.loading") }}</div>
      <div v-else-if="error" class="px-3 py-4 text-xs text-coral">{{ error }}</div>
      <div v-else-if="!filtered.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("add.noConfigs") }}</div>
      <button v-for="r in filtered" :key="r.path" type="button" class="w-full text-left px-3 py-2 flex items-start gap-2.5 hover:bg-warm-100 dark:hover:bg-warm-800/60" :class="modelValue === r.path ? 'bg-iolite/10' : ''" :data-test="`add-config-${r.name}`" @click="$emit('update:modelValue', r.path)">
        <span class="kt-v2-b mt-0.5 w-3.5 h-3.5 rounded-full border-2 shrink-0" :class="modelValue === r.path ? 'border-iolite bg-iolite' : 'border-warm-300 dark:border-warm-600'" />
        <span class="min-w-0 flex-1">
          <span class="block text-[13px] font-medium text-warm-800 dark:text-warm-100 truncate">{{ r.name }}</span>
          <span v-if="r.description" class="block text-[11px] text-warm-500 line-clamp-2">{{ r.description }}</span>
          <span class="block text-[10px] font-mono text-warm-400 truncate">{{ r.path }}</span>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useV2T } from "@/components/session-v2/model/v2Strings"
import { configAPI } from "@/utils/api"

/** A filterable single-choice list of creature configs (`kind="creature"`) or terrarium recipes (`kind="terrarium"`); `modelValue` is the chosen path. */
const props = defineProps({
  kind: { type: String, default: "creature" },
  modelValue: { type: String, default: "" },
})
const emit = defineEmits(["update:modelValue", "picked"])

const t = useV2T()
const rows = ref([])
const loading = ref(false)
const error = ref("")
const query = ref("")

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((r) => `${r.name} ${r.description || ""} ${r.path}`.toLowerCase().includes(q))
})

let request = 0
async function load() {
  const id = ++request
  loading.value = true
  error.value = ""
  try {
    const data = props.kind === "terrarium" ? await configAPI.listTerrariums() : await configAPI.listCreatures()
    if (id === request) rows.value = Array.isArray(data) ? data : []
  } catch (err) {
    if (id === request) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (id === request) loading.value = false
  }
}

watch(
  () => props.modelValue,
  (path) => {
    const row = rows.value.find((r) => r.path === path)
    if (row) emit("picked", row)
  },
)
watch(() => props.kind, load)
onMounted(load)
</script>
