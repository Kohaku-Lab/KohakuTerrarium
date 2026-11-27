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
        <KButton :disabled="!provider || loading" @click="load(true)"><span class="i-carbon-renew" :class="{ 'animate-spin': loading }" />{{ t("discovery.refresh") }}</KButton>
      </div>
      <p v-if="fetchedAt" class="kt-text-caption text-warm-500" data-fetched-at>{{ t("discovery.updated", { time: formatFetchedAt(fetchedAt) }) }}</p>
      <p v-if="provider === 'opencode-go' && available.some((b) => b.name === provider && b.built_in && !b.available)" class="kt-text-caption text-warm-500">{{ t("discovery.public") }}</p>
      <el-input v-model="query" clearable :placeholder="t('discovery.search')" :aria-label="t('discovery.search')"
        ><template #prefix><span class="i-carbon-search" /></template
      ></el-input>
      <div class="kt-v2-card overflow-y-auto max-h-72 divide-y divide-[var(--v2-line)]" :aria-busy="busy">
        <p v-if="busy || error || !matches.length" class="px-4 py-6 text-center kt-text-body" role="status">{{ busy ? t("discovery.loading") : error || t(query ? "settings.models.noMatch" : "discovery.empty") }}</p>
        <button v-for="row in matches.slice(0, limit)" :key="JSON.stringify([row.id, row.preset?.name])" type="button" class="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-iolite/10 focus-visible:bg-iolite/10" @click="choose(row)">
          <span class="min-w-0 flex-1 flex flex-col gap-0.5">
            <span class="flex items-center gap-1.5 min-w-0">
              <strong class="truncate kt-text-body">{{ row.preset?.model && row.preset.model !== row.id ? row.preset.name : row.name || row.id }}</strong>
              <span v-if="typeof row.has_preset === 'boolean'" class="shrink-0" :class="row.has_preset ? 'chip-aqua' : 'chip-iolite'" :data-preset-badge="row.has_preset ? 'existing' : 'new'">{{ t(row.has_preset ? "discovery.hasPreset" : "discovery.newPreset") }}</span>
            </span>
            <span v-if="row.preset?.model && row.preset.model !== row.id" class="block truncate kt-text-caption font-mono text-warm-500">{{ row.preset.model }}</span>
            <span v-else-if="row.name && row.name !== row.id" class="block truncate kt-text-caption font-mono text-warm-500">{{ row.id }}</span>
            <span v-if="row.reasoning_levels?.length" class="flex flex-wrap items-center gap-1" :title="row.reasoning_levels.join(', ')" data-reasoning-levels>
              <span class="kt-text-caption text-warm-500 mr-0.5">{{ t("discovery.reasoning") }}</span>
              <span v-for="level in row.reasoning_levels.slice(0, LEVELS_SHOWN)" :key="level" class="px-1.5 rounded border border-warm-200 dark:border-warm-700 text-[10px] leading-4 font-mono text-warm-600 dark:text-warm-300" data-reasoning-level>{{ level }}</span>
              <span v-if="row.reasoning_levels.length > LEVELS_SHOWN" class="text-[10px] text-warm-500">+{{ row.reasoning_levels.length - LEVELS_SHOWN }}</span>
            </span>
          </span>
          <span v-if="row.context_window || row.max_context" class="shrink-0 kt-text-caption text-warm-500" data-context>{{ t("settings.models.metaContext", { size: formatCount(row.context_window || row.max_context) }) }}</span>
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
import { computed, ref, shallowRef, watch } from "vue"
import KButton from "@/components/common/ActionButton.vue"
import { formatCount } from "@/components/settings/models/presetModel"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import { bindProviderPresets, loadProviderModels, providerModelsEntry } from "@/utils/providerModels"
const props = defineProps({ modelValue: Boolean, presets: { type: Array, default: () => [] }, backends: { type: Array, default: () => [] } })
const emit = defineEmits(["update:modelValue", "pick", "existing", "configure"])
const { t } = useI18n()
const LEVELS_SHOWN = 6
const limit = ref(50)
const provider = ref(""),
  query = ref(""),
  entry = shallowRef(null)
const rows = computed(() =>
  bindProviderPresets(entry.value?.rows.value || [], props.presets, provider.value).flatMap((row) => {
    if (!row.preset_choices?.length) return row
    return row.preset_choices.map((name) => ({ ...row, name, preset: { ...row.preset, name } }))
  }),
)
const loading = computed(() => Boolean(entry.value?.loading.value))
const busy = computed(() => loading.value && !entry.value.hasLoaded.value)
const error = computed(() => (entry.value?.failed.value ? entry.value.errorDetail.value || t("discovery.failed") : ""))
const fetchedAt = computed(() => entry.value?.fetchedAt.value || "")
const available = computed(() => props.backends.filter((b) => b.model_discovery))
const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return rows.value.filter((row) => [row.id, row.name, row.preset?.name, row.preset?.model].join(" ").toLowerCase().includes(needle))
})
watch([query, provider, () => props.modelValue], () => {
  limit.value = 50
})
// Cached rows show at once; a stale list or the Refresh button refetches.
function load(refresh = false) {
  if (!props.modelValue || !provider.value) {
    entry.value = null
    return
  }
  entry.value = providerModelsEntry(settingsAPI, provider.value)
  loadProviderModels(settingsAPI, provider.value, { refresh })
}
function formatFetchedAt(iso) {
  const time = new Date(iso)
  return Number.isNaN(time.getTime()) ? iso : time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
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
  if (row.has_preset && row.preset?.name) emit("existing", { provider: provider.value, name: row.preset.name })
  else emit("pick", row.preset)
  emit("update:modelValue", false)
}
</script>
