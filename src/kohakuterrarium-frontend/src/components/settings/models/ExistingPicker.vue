<template>
  <el-dialog :model-value="modelValue" :title="t('settings.models.fromExisting.title')" width="min(32rem, 94vw)" @update:model-value="(value) => emit('update:modelValue', value)">
    <p class="kt-text-caption text-warm-500 mb-3">{{ t("settings.models.fromExisting.hint") }}</p>
    <el-input v-model="query" size="small" clearable :placeholder="t('settings.models.search')" class="mb-2" data-picker-search>
      <template #prefix><span class="i-carbon-search" /></template>
    </el-input>
    <div class="kt-v2-card max-h-72 overflow-y-auto divide-y divide-[var(--v2-line)]">
      <button v-for="preset in matches" :key="presetKey(preset)" type="button" class="w-full px-3 py-2 text-left flex flex-col transition-colors" :class="presetKey(preset) === pickedKey ? 'bg-iolite/10' : 'hover:bg-warm-200/60 dark:hover:bg-warm-800'" data-picker-row @click="pick(preset)">
        <span class="kt-text-body font-medium text-warm-800 dark:text-warm-100 truncate">{{ preset.name }}</span>
        <span class="kt-text-caption font-mono text-warm-500 truncate">{{ providerOf(preset) }} · {{ preset.model }}</span>
      </button>
      <p v-if="!matches.length" class="px-3 py-6 text-center kt-text-caption text-warm-400">{{ t("settings.models.noMatch") }}</p>
    </div>
    <label class="mt-3 flex items-center gap-2">
      <span class="kt-text-caption text-warm-500 shrink-0">{{ t("settings.models.fromExisting.provider") }}</span>
      <el-select v-model="provider" size="small" class="flex-1" :disabled="!picked">
        <el-option v-for="backend in backends" :key="backend.name" :value="backend.name" :label="backend.name" />
      </el-select>
    </label>
    <template #footer>
      <KButton variant="ghost" @click="emit('update:modelValue', false)">{{ t("common.cancel") }}</KButton>
      <KButton variant="primary" :disabled="!picked" data-picker-confirm @click="confirm">{{ t("settings.models.fromExisting.create") }}</KButton>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import KButton from "@/components/common/ActionButton.vue"
import { presetKey, providerOf } from "@/components/settings/models/presetModel"
import { useI18n } from "@/utils/i18n"

/** Picks the preset a new one is copied from, and the provider the copy belongs to. */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  presets: { type: Array, default: () => [] },
  backends: { type: Array, default: () => [] },
})
const emit = defineEmits(["update:modelValue", "pick"])

const { t } = useI18n()
const query = ref("")
const picked = ref(null)
const provider = ref("")
const pickedKey = computed(() => presetKey(picked.value))

const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return props.presets.filter((preset) => !needle || `${preset.name} ${preset.model} ${providerOf(preset)}`.toLowerCase().includes(needle))
})

function pick(preset) {
  picked.value = preset
  provider.value = providerOf(preset)
}

function confirm() {
  if (!picked.value) return
  emit("pick", { preset: picked.value, provider: provider.value })
  emit("update:modelValue", false)
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      query.value = ""
      picked.value = null
      provider.value = ""
    }
  },
)
</script>
