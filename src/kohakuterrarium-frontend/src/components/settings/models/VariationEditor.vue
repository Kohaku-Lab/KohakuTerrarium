<template>
  <SettingsGroup :title="t('settings.models.variations.title')" :description="t('settings.models.variations.hint')" data-test="variation-editor">
    <template v-if="!readonly" #action>
      <el-dropdown trigger="click" @command="addKnob">
        <KButton size="sm" data-variation-add><span class="i-carbon-add" />{{ t("settings.models.variations.addKnob") }}<span class="i-carbon-chevron-down" /></KButton>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="template in VARIATION_TEMPLATES" :key="template.id" :command="template.id">{{ t(`settings.models.variations.tpl.${template.id}`) }}</el-dropdown-item>
            <el-dropdown-item command="blank" divided>{{ t("settings.models.variations.blank") }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </template>

    <p v-if="!groups.length" class="px-4 py-6 text-center kt-text-caption text-warm-500">{{ t("settings.models.variations.empty") }}</p>

    <KnobCard v-for="(group, g) in groups" :key="g" v-model:group="groups[g]" :issues="issues.filter((issue) => issue.group === g)" :readonly="readonly" @remove="groups.splice(g, 1)" />

    <VariationTry v-if="groups.length" :groups="groups" :base="base" :provider="provider" :name="name" />
  </SettingsGroup>
</template>

<script setup>
import { computed } from "vue"

import KnobCard from "@/components/settings/models/KnobCard.vue"
import VariationTry from "@/components/settings/models/VariationTry.vue"
import KButton from "@/components/common/ActionButton.vue"
import { validateGroups, VARIATION_TEMPLATES } from "@/components/settings/models/variationModel"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import { useI18n } from "@/utils/i18n"

/** Edits a preset's variation knobs and previews what a choice of options sends. */
const groups = defineModel("groups", { type: Array, required: true })
defineProps({
  base: { type: Object, required: true },
  provider: { type: String, default: "" },
  name: { type: String, default: "" },
  readonly: { type: Boolean, default: false },
})

const { t } = useI18n()
const issues = computed(() => validateGroups(groups.value))

function addKnob(id) {
  if (id === "blank") {
    groups.value.push({ name: "", target: "", type: "text", open: true, options: [] })
    return
  }
  const template = VARIATION_TEMPLATES.find((item) => item.id === id)
  if (template) groups.value.push(template.build())
}
</script>
