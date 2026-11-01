<template>
  <div class="flex-1 min-h-0 flex flex-col" data-test="models-section">
    <header class="px-8 pt-6 pb-3 max-md:hidden">
      <h2 class="text-lg font-semibold text-warm-800 dark:text-warm-100">{{ title }}</h2>
      <p v-if="hint" class="mt-1 text-sm text-warm-500 leading-relaxed">{{ hint }}</p>
    </header>
    <div class="flex-1 min-h-0 px-8 pb-6 max-md:px-3 max-md:pb-3 max-md:pt-3">
      <div class="kt-v2-card h-full flex overflow-hidden">
        <aside v-if="!isCompact || !models.open.value" class="kt-v2-edge min-h-0" :class="isCompact ? 'w-full' : 'w-80 shrink-0 border-r'">
          <ModelList v-model:query="models.query.value" v-model:filter="models.filter.value" :groups="models.groups.value" :total="models.presets.value.length" :shown-count="models.shownCount.value" :selected-key="models.selectedKey.value" @select="models.select" @new-blank="models.startBlank" @new-existing="pickerOpen = true" @clone="(preset) => models.startFrom(preset)" @set-default="models.setDefault" @delete="models.remove" />
        </aside>

        <section v-if="!isCompact || models.open.value" class="flex-1 min-w-0 min-h-0 flex flex-col">
          <KButton v-if="isCompact" size="sm" variant="ghost" class="self-start m-3 mb-0 shrink-0" data-model-back @click="models.back"><span class="i-carbon-arrow-left" />{{ t("settings.models.backToList") }}</KButton>
          <ModelEditor v-if="models.open.value" :preset="models.editing.value" :backends="backends" :mode="models.mode.value" @save="models.save" @cancel="models.close" @clone="(preset) => models.startFrom(preset)" @delete="models.remove" @set-default="models.setDefault" />
          <div v-else class="flex-1 overflow-y-auto p-5 flex flex-col items-center justify-center gap-3 text-center" data-model-empty>
            <span class="i-carbon-model-alt text-4xl text-warm-300 dark:text-warm-600" />
            <p class="kt-text-body text-warm-600 dark:text-warm-300">{{ t("settings.models.empty") }}</p>
            <div class="flex items-center gap-2">
              <KButton variant="primary" @click="models.startBlank"><span class="i-carbon-add" />{{ t("settings.models.newBlank") }}</KButton>
              <KButton @click="pickerOpen = true">{{ t("settings.models.newFromExisting") }}</KButton>
            </div>
            <p class="kt-text-caption text-warm-500">{{ t("settings.models.storageHint") }}</p>
          </div>
        </section>
      </div>
    </div>

    <ExistingPicker v-model="pickerOpen" :presets="models.presets.value" :backends="backends" @pick="({ preset, provider }) => models.startFrom(preset, provider)" />
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue"

import KButton from "@/components/common/ActionButton.vue"
import ExistingPicker from "@/components/settings/models/ExistingPicker.vue"
import ModelEditor from "@/components/settings/models/ModelEditor.vue"
import ModelList from "@/components/settings/models/ModelList.vue"
import { useModelPresets } from "@/components/settings/models/useModelPresets"
import { useDensity } from "@/composables/useDensity"
import { useI18n } from "@/utils/i18n"

/** Settings → Models: the preset list and the editor of the open preset, as a split view (a list-then-page flow on phones). */
const props = defineProps({
  title: { type: String, default: "" },
  hint: { type: String, default: "" },
  backends: { type: Array, default: () => [] },
})

const { t } = useI18n()
const { isCompact } = useDensity()
const models = useModelPresets()
const pickerOpen = ref(false)

onMounted(models.load)
// A key saved or removed changes which presets are ready.
watch(() => props.backends, models.load)

defineExpose({ models })
</script>
