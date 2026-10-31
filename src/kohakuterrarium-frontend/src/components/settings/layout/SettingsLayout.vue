<template>
  <div v-if="isCompact" class="kt-v2-canvas h-full flex flex-col min-h-0" data-settings-layout>
    <PhonePage v-if="!showList && current" :title="sectionLabel(current)" :icon="current.icon" :back-label="t('common.back')" :scroll="current.width !== 'fill'" test-id="settings-page" @back="showList = true">
      <slot />
    </PhonePage>
    <nav v-else class="flex-1 min-h-0 overflow-y-auto" :aria-label="t('common.settings')" data-settings-nav>
      <div class="px-4 pt-3 pb-1">
        <el-input v-model="query" size="small" clearable :placeholder="t('settings.nav.search')" data-settings-search @keydown.esc="query = ''">
          <template #prefix><span class="i-carbon-search" /></template>
        </el-input>
      </div>
      <template v-for="group in shown" :key="group.id">
        <div class="px-4 pt-4 pb-1 text-[11px] uppercase tracking-wider text-warm-400">{{ groupLabel(group) }}</div>
        <button v-for="section in group.sections" :key="section.id" type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-14 flex items-center gap-3 px-4 border-b text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-section="section.id" @click="pick(section.id)">
          <span :class="section.icon" class="text-lg text-warm-500 shrink-0" />
          <span class="flex-1 min-w-0 text-[15px] text-warm-800 dark:text-warm-100">{{ sectionLabel(section) }}</span>
          <span v-if="badgeOf(section)" class="w-1.5 h-1.5 rounded-full bg-amber shrink-0" :data-badge="section.id" />
          <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
        </button>
      </template>
      <p v-if="!shown.length" class="px-4 py-6 text-center text-sm text-warm-400">{{ t("settings.nav.empty") }}</p>
    </nav>
  </div>

  <div v-else class="kt-v2-canvas h-full flex min-h-0" data-settings-layout>
    <nav class="kt-v2-panel kt-v2-edge w-56 shrink-0 flex flex-col gap-0.5 p-3 border-r overflow-y-auto" :aria-label="t('common.settings')" data-settings-nav>
      <el-input v-model="query" size="small" clearable class="mb-1" :placeholder="t('settings.nav.search')" data-settings-search @keydown.esc="query = ''">
        <template #prefix><span class="i-carbon-search" /></template>
      </el-input>
      <template v-for="group in shown" :key="group.id">
        <div class="px-3 pt-3 pb-1 text-[11px] uppercase tracking-wider text-warm-400">{{ groupLabel(group) }}</div>
        <button v-for="section in group.sections" :key="section.id" type="button" class="h-9 px-3 rounded-lg flex items-center gap-2.5 text-sm text-left transition-colors" :class="section.id === modelValue ? 'bg-iolite/10 text-iolite dark:text-iolite-light font-medium' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-800'" :aria-current="section.id === modelValue ? 'page' : undefined" :data-section="section.id" @click="pick(section.id)">
          <span :class="section.icon" class="shrink-0" />
          <span class="flex-1 min-w-0 truncate">{{ sectionLabel(section) }}</span>
          <span v-if="badgeOf(section)" class="w-1.5 h-1.5 rounded-full bg-amber shrink-0" :data-badge="section.id" />
        </button>
      </template>
      <p v-if="!shown.length" class="px-3 py-4 text-xs text-warm-400">{{ t("settings.nav.empty") }}</p>
    </nav>
    <div class="flex-1 min-w-0" :class="current?.width === 'fill' ? 'flex flex-col overflow-hidden' : 'overflow-y-auto'"><slot /></div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import PhonePage from "@/components/session-v2/phone/PhonePage.vue"
import { useDensity } from "@/composables/useDensity"
import { consumeDirectOpen } from "@/stores/settingsNav"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  groups: { type: Array, required: true },
  modelValue: { type: String, default: "" },
})
const emit = defineEmits(["update:modelValue"])

const { t } = useI18n()
const { isCompact } = useDensity()
const query = ref("")
// On a phone the section list is the first screen, unless a section was opened on purpose.
const showList = ref(false)

const current = computed(() => props.groups.flatMap((group) => group.sections).find((section) => section.id === props.modelValue) || null)

const sectionLabel = (section) => (section.labelKey ? t(section.labelKey) : section.label)
const groupLabel = (group) => (group.labelKey ? t(group.labelKey) : group.label)
const badgeOf = (section) => (section.badge ? section.badge() : null)

function matches(section, group, needle) {
  return [sectionLabel(section), groupLabel(group), ...(section.keywords || [])].join(" ").toLowerCase().includes(needle)
}

const shown = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return props.groups
  return props.groups.map((group) => ({ ...group, sections: group.sections.filter((section) => matches(section, group, needle)) })).filter((group) => group.sections.length)
})

function pick(id) {
  emit("update:modelValue", id)
  showList.value = false
}

watch(
  () => props.modelValue,
  () => {
    showList.value = false
  },
)

onMounted(() => {
  showList.value = isCompact.value && !consumeDirectOpen()
})
</script>
