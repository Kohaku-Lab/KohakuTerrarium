<template>
  <SettingsLayout :groups="groups" :model-value="section" data-test="v2-settings" @update:model-value="setSection">
    <component :is="SECTION_COMPONENTS[section]" :key="section" />
  </SettingsLayout>
</template>

<script setup>
import { computed, ref } from "vue"

import { SETTINGS_GROUPS } from "@/components/session-v2/model/settings/settingsModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import CostSection from "@/components/session-v2/settings/CostSection.vue"
import EnvSection from "@/components/session-v2/settings/EnvSection.vue"
import ExtensionsSection from "@/components/session-v2/settings/ExtensionsSection.vue"
import ModulesSection from "@/components/session-v2/settings/ModulesSection.vue"
import ModelSection from "@/components/session-v2/settings/ModelSection.vue"
import TriggersSection from "@/components/session-v2/settings/TriggersSection.vue"
import WorkspaceSection from "@/components/session-v2/settings/WorkspaceSection.vue"
import SettingsLayout from "@/components/settings/layout/SettingsLayout.vue"
import { useI18n } from "@/utils/i18n"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The session's Settings tab: the same grouped, searchable section list as the global Settings
 * page, with one section's form beside it. Phones list the sections and open one as its own page.
 */
const SECTION_COMPONENTS = {
  model: ModelSection,
  modules: ModulesSection,
  env: EnvSection,
  triggers: TriggersSection,
  cost: CostSection,
  workspace: WorkspaceSection,
  extensions: ExtensionsSection,
}
const KEY = "kt.v2.settings.section"

const t = useV2T()
const { t: sharedT } = useI18n()
const sectionTitle = (id) => (id === "modules" ? sharedT("settings.section.modules") : t(`set.${id}.title`))

const groups = computed(() =>
  SETTINGS_GROUPS.map((group) => ({
    id: group.id,
    label: t(group.labelKey),
    sections: group.sections.map((section) => ({ ...section, label: sectionTitle(section.id) })),
  })),
)

const stored = readLocalPref(KEY)
const section = ref(SECTION_COMPONENTS[stored] ? stored : "model")

function setSection(id) {
  section.value = id
  writeLocalPref(KEY, id)
}
</script>
