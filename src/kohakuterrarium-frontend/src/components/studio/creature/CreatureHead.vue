<template>
  <div class="flex items-center gap-2 w-full min-w-0">
    <button class="h-8 px-2 rounded-lg inline-flex items-center gap-1.5 text-[13px] text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" :title="t('studio.frame.back')" data-test="creature-back" @click="$emit('back')"><span class="i-carbon-arrow-left" />{{ t("studioApp.nav.overview") }}</button>
    <span class="text-warm-400">/</span>
    <span class="i-carbon-bee text-iolite shrink-0" />
    <h1 class="text-[14px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ name }}</h1>
    <span v-if="dirty" class="flex items-center gap-1 text-[11px] text-iolite dark:text-iolite-light shrink-0" :title="t('studio.frame.unsaved')">
      <span class="w-1.5 h-1.5 rounded-full bg-iolite" />
      {{ t("studio.frame.unsaved") }}
    </span>

    <div class="flex-1" />

    <KButton size="sm" variant="secondary" icon="i-carbon-play" :disabled="saving" :title="dirty ? t('studioApp.run.saveFirst') : t('studioApp.run.hint')" data-test="creature-run" @click="$emit('run')">
      {{ t("studioApp.run.label") }}
    </KButton>
    <KButton size="sm" variant="secondary" :disabled="!dirty || saving" @click="$emit('discard')">
      {{ t("studio.frame.discard") }}
    </KButton>
    <KButton size="sm" variant="primary" :icon="saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-save'" :disabled="!dirty || saving" @click="$emit('save')">
      {{ saving ? t("studio.frame.saving") : t("studio.frame.save") }}
    </KButton>
  </div>
</template>

<script setup>
import KButton from "@/components/studio/common/KButton.vue"
import { useI18n } from "@/utils/i18n"

/** The creature editor's head: where it sits, whether it has unsaved changes, and Run / Discard / Save. */
const { t } = useI18n()

defineProps({
  name: { type: String, default: "" },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
})

defineEmits(["back", "save", "discard", "run"])
</script>
