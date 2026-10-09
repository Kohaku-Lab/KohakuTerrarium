<template>
  <div v-if="isCompact" class="kt-v2-canvas h-full flex flex-col min-h-0" data-test="v2-settings">
    <PhonePage v-if="phoneSection" :title="t(`set.${phoneSection}.title`)" :icon="iconOf(phoneSection)" :back-label="t('phone.back')" :test-id="`v2-settings-page-${phoneSection}`" @back="phoneSection = null">
      <component :is="SECTION_COMPONENTS[phoneSection]" :key="phoneSection" />
    </PhonePage>
    <nav v-else class="flex-1 min-h-0 overflow-y-auto py-2" :aria-label="t('set.nav')">
      <button v-for="s in SETTINGS_SECTIONS" :key="s.id" type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-14 flex items-center gap-3 px-4 border-b text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`v2-settings-nav-${s.id}`" @click="phoneSection = s.id">
        <span :class="s.icon" class="text-lg text-warm-500 shrink-0" />
        <span class="flex-1 min-w-0 text-[15px] text-warm-800 dark:text-warm-100">{{ t(`set.${s.id}.title`) }}</span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
    </nav>
  </div>

  <div v-else class="kt-v2-canvas h-full flex min-h-0" data-test="v2-settings">
    <nav class="kt-v2-panel kt-v2-edge w-56 shrink-0 flex flex-col gap-0.5 p-3 border-r" :aria-label="t('set.nav')">
      <button v-for="s in SETTINGS_SECTIONS" :key="s.id" class="h-9 px-3 rounded-lg flex items-center gap-2.5 text-sm text-left transition-colors" :class="section === s.id ? 'bg-iolite/10 text-iolite dark:text-iolite-light font-medium' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-800'" :aria-current="section === s.id ? 'page' : undefined" :data-test="`v2-settings-nav-${s.id}`" @click="setSection(s.id)"><span :class="s.icon" class="shrink-0" />{{ t(`set.${s.id}.title`) }}</button>
    </nav>
    <div class="flex-1 min-w-0 overflow-y-auto">
      <component :is="SECTION_COMPONENTS[section]" :key="section" />
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"

import { SETTINGS_SECTIONS } from "@/components/session-v2/model/settings/settingsModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import PhonePage from "@/components/session-v2/phone/PhonePage.vue"
import CostSection from "@/components/session-v2/settings/CostSection.vue"
import EnvSection from "@/components/session-v2/settings/EnvSection.vue"
import ExtensionsSection from "@/components/session-v2/settings/ExtensionsSection.vue"
import ModelSection from "@/components/session-v2/settings/ModelSection.vue"
import TriggersSection from "@/components/session-v2/settings/TriggersSection.vue"
import WorkspaceSection from "@/components/session-v2/settings/WorkspaceSection.vue"
import { useDensity } from "@/composables/useDensity"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The Settings tab: section nav on the left, one whole-page form on the
 * right. Phones list the sections and open one as its own page.
 */
const SECTION_COMPONENTS = {
  model: ModelSection,
  env: EnvSection,
  triggers: TriggersSection,
  cost: CostSection,
  workspace: WorkspaceSection,
  extensions: ExtensionsSection,
}
const KEY = "kt.v2.settings.section"

const t = useV2T()
const { isCompact } = useDensity()
const stored = readLocalPref(KEY)
const section = ref(SECTION_COMPONENTS[stored] ? stored : "model")
const phoneSection = ref(null)

const iconOf = (id) => SETTINGS_SECTIONS.find((s) => s.id === id)?.icon || ""

function setSection(id) {
  section.value = id
  writeLocalPref(KEY, id)
}
</script>
