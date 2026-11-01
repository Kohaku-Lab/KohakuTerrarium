<template>
  <div class="h-full flex flex-col min-h-0" data-test="model-list">
    <div class="kt-v2-line border-b p-3 flex flex-col gap-2 shrink-0">
      <div class="flex items-center gap-2">
        <el-input :model-value="query" size="small" clearable class="flex-1" :placeholder="t('settings.models.search')" data-model-search @update:model-value="(value) => emit('update:query', value)">
          <template #prefix><span class="i-carbon-search" /></template>
        </el-input>
        <el-dropdown trigger="click" @command="onNew">
          <KButton variant="primary" size="sm" data-model-new><span class="i-carbon-add" />{{ t("settings.models.new") }}<span class="i-carbon-chevron-down" /></KButton>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="blank">{{ t("settings.models.newBlank") }}</el-dropdown-item>
              <el-dropdown-item command="existing" data-model-from-existing>{{ t("settings.models.newFromExisting") }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <el-segmented :model-value="filter" :options="filterOptions" size="small" block data-model-filter @change="(value) => emit('update:filter', value)" />
      <div class="kt-text-caption text-warm-500">{{ t("settings.models.count", { shown: shownCount, total }) }}</div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto p-2">
      <section v-for="group in groups" :key="group.provider" class="mb-3" :data-provider="group.provider">
        <h3 class="px-2 py-1 flex items-center gap-2 kt-text-caption uppercase tracking-wider text-warm-500">
          <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="group.ready ? 'bg-aquamarine' : 'bg-warm-300 dark:bg-warm-600'" :title="group.ready ? t('settings.models.ready') : t('settings.models.noKey')" />
          <span class="flex-1 min-w-0 truncate">{{ group.provider }}</span>
          <span>{{ group.presets.length }}</span>
        </h3>
        <div v-for="preset in group.presets" :key="presetKey(preset)" class="group relative">
          <button type="button" class="w-full min-h-14 px-3 py-2 rounded-lg text-left flex flex-col gap-0.5 transition-colors" :class="[presetKey(preset) === selectedKey ? 'bg-iolite/10' : 'hover:bg-warm-200/60 dark:hover:bg-warm-800', preset.available === false && 'opacity-60']" :aria-current="presetKey(preset) === selectedKey ? 'true' : undefined" data-model-row @click="emit('select', preset)">
            <span class="flex items-center gap-1.5 min-w-0 pr-7">
              <span class="kt-text-body font-medium truncate" :class="presetKey(preset) === selectedKey ? 'text-iolite dark:text-iolite-light' : 'text-warm-800 dark:text-warm-100'">{{ preset.name }}</span>
              <span v-if="preset.is_default" class="chip-aqua shrink-0">{{ t("settings.models.isDefault") }}</span>
              <span v-if="preset.source === 'user'" class="chip-iolite shrink-0">{{ t("settings.models.user") }}</span>
            </span>
            <span class="kt-text-caption font-mono text-warm-500 truncate">{{ preset.model }}</span>
            <span class="kt-text-caption text-warm-400 truncate">{{ meta(preset) }}</span>
          </button>
          <el-dropdown trigger="click" class="absolute top-2 right-2" @command="(command) => onRow(command, preset)">
            <button type="button" class="btn-icon !w-7 !h-7 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100" :aria-label="t('settings.models.actions')" data-model-menu><span class="i-carbon-overflow-menu-horizontal" /></button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="clone">{{ t("settings.models.clone") }}</el-dropdown-item>
                <el-dropdown-item v-if="!preset.is_default" command="default">{{ t("settings.models.setAsDefault") }}</el-dropdown-item>
                <el-dropdown-item v-if="preset.source === 'user'" command="delete" divided class="!text-coral">{{ t("common.delete") }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </section>
      <p v-if="!groups.length" class="px-3 py-8 text-center kt-text-caption text-warm-400">{{ t("settings.models.noMatch") }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KButton from "@/components/common/ActionButton.vue"
import { FILTERS, formatCount, knobCount, presetKey } from "@/components/settings/models/presetModel"
import { useI18n } from "@/utils/i18n"

/** The grouped, searchable preset list with its filter, "new" menu and per-row actions. */
defineProps({
  groups: { type: Array, required: true },
  total: { type: Number, default: 0 },
  shownCount: { type: Number, default: 0 },
  selectedKey: { type: String, default: "" },
  query: { type: String, default: "" },
  filter: { type: String, default: "all" },
})
const emit = defineEmits(["update:query", "update:filter", "select", "new-blank", "new-existing", "clone", "set-default", "delete"])

const { t } = useI18n()

const filterOptions = computed(() => FILTERS.map((value) => ({ value, label: t(`settings.models.filter.${value}`) })))

function meta(preset) {
  const parts = []
  if (preset.max_context) parts.push(t("settings.models.metaContext", { size: formatCount(preset.max_context) }))
  if (preset.max_output) parts.push(t("settings.models.metaOutput", { size: formatCount(preset.max_output) }))
  const knobs = knobCount(preset)
  if (knobs) parts.push(t("settings.models.metaKnobs", { count: knobs }))
  return parts.join(" · ")
}

function onNew(command) {
  emit(command === "blank" ? "new-blank" : "new-existing")
}

function onRow(command, preset) {
  emit(command === "default" ? "set-default" : command, preset)
}
</script>
