<template>
  <el-dialog :model-value="modelValue" :title="t('discovery.title')" width="min(35rem, 94vw)" @update:model-value="emit('update:modelValue', $event)">
    <div class="flex flex-col gap-3">
      <div class="flex items-end gap-2">
        <label class="flex flex-col gap-1 flex-1 min-w-0"
          ><span class="kt-text-caption text-warm-500">{{ t("settings.models.field.provider") }}</span>
          <el-select v-model="provider" :aria-label="t('settings.models.field.provider')">
            <el-option v-for="backend in available" :key="backend.name" :value="backend.name" :label="backend.name" />
          </el-select>
        </label>
        <KButton :disabled="!provider || busy" @click="load"><span class="i-carbon-renew" />{{ t("discovery.refresh") }}</KButton>
      </div>
      <p v-if="provider === 'opencode-go' && available.some((b) => b.name === provider && b.built_in && !b.available)" class="kt-text-caption text-warm-500">{{ t("discovery.public") }}</p>
      <el-input v-model="query" clearable :placeholder="t('discovery.search')" :aria-label="t('discovery.search')"
        ><template #prefix><span class="i-carbon-search" /></template
      ></el-input>
      <div class="kt-v2-card overflow-y-auto max-h-72 divide-y divide-[var(--v2-line)]" :aria-busy="busy">
        <p v-if="busy || error || !matches.length" class="px-4 py-6 text-center kt-text-body" role="status">{{ busy ? t("discovery.loading") : error || t(query ? "settings.models.noMatch" : "discovery.empty") }}</p>
        <button v-for="row in matches.slice(0, limit)" :key="row.id" type="button" class="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-iolite/10 focus-visible:bg-iolite/10" @click="choose(row)">
          <span class="min-w-0 flex-1"
            ><strong class="block truncate kt-text-body">{{ row.name || row.id }}</strong
            ><span v-if="row.name !== row.id" class="block truncate kt-text-caption font-mono text-warm-500">{{ row.id }}</span></span
          >
          <span v-if="row.max_context" class="shrink-0 kt-text-caption text-warm-500">{{ t("settings.models.metaContext", { size: formatCount(row.max_context) }) }}</span>
          <span class="i-carbon-chevron-right text-warm-500" />
        </button>
        <KButton v-if="matches.length > limit" variant="ghost" @click="limit += 50">{{ t("discovery.more", { count: matches.length - limit }) }}</KButton>
      </div>
    </div>
    <template #footer
      ><KButton variant="ghost" @click="emit('configure', provider)"><span class="i-carbon-settings" />{{ t("discovery.configure") }}</KButton></template
    >
  </el-dialog>
</template>
<script setup>
import { computed, ref, watch } from "vue"
import KButton from "@/components/common/ActionButton.vue"
import { formatCount } from "@/components/settings/models/presetModel"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
const props = defineProps({ modelValue: Boolean, backends: { type: Array, default: () => [] } })
const emit = defineEmits(["update:modelValue", "pick", "configure"])
const { t } = useI18n()
const limit = ref(50)
const provider = ref(""),
  query = ref(""),
  rows = ref([]),
  error = ref(""),
  busy = ref(false)
const available = computed(() => props.backends.filter((b) => b.model_discovery))
const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return rows.value.filter((row) => (row.id + " " + row.name).toLowerCase().includes(needle))
})
let generation = 0
watch([query, provider, () => props.modelValue], () => {
  limit.value = 50
})
async function load() {
  const request = ++generation
  rows.value = []
  error.value = ""
  busy.value = false
  if (!props.modelValue || !provider.value) return
  busy.value = true
  try {
    const data = await settingsAPI.discoverModels(provider.value)
    if (request === generation) rows.value = data.models
  } catch (cause) {
    if (request === generation) error.value = cause.response?.data?.detail || t("discovery.failed")
  } finally {
    if (request === generation) busy.value = false
  }
}
watch(
  available,
  (items) => {
    if (!items.some((b) => b.name === provider.value)) provider.value = (items.find((b) => b.available) || items.find((b) => b.name === "opencode-go") || items[0])?.name || ""
  },
  { immediate: true },
)
watch(
  [() => props.modelValue, provider],
  () => {
    query.value = ""
    load()
  },
  { immediate: true },
)
function choose(row) {
  emit("pick", row.preset)
  emit("update:modelValue", false)
}
</script>
