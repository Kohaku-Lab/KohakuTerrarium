<template>
  <div class="flex items-center gap-2 w-full">
    <button class="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-warm-200 dark:hover:bg-warm-700 text-warm-600 dark:text-warm-300" :title="t('studio.frame.back')" @click="$emit('back')">
      <div class="i-carbon-arrow-left text-sm" />
    </button>

    <div class="flex items-center gap-2 min-w-0">
      <div class="i-carbon-bot text-warm-500 text-base shrink-0" />
      <span class="font-mono text-sm text-warm-800 dark:text-warm-200 truncate">
        {{ name }}
      </span>
      <span class="text-xs text-warm-500 dark:text-warm-500">/ creature</span>
      <span v-if="dirty" class="flex items-center gap-1 text-[11px] text-iolite dark:text-iolite-light" :title="t('studio.frame.unsaved')">
        <span class="w-1.5 h-1.5 rounded-full bg-iolite" />
        {{ t("studio.frame.unsaved") }}
      </span>
    </div>

    <div class="flex-1" />

    <KButton size="sm" variant="ghost" icon="i-carbon-play" :disabled="true" :title="t('studio.creature.head.testDriveComingSoon')">
      {{ t("studio.creature.head.testDrive") }}
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

const { t } = useI18n()

defineProps({
  name: { type: String, default: "" },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
})

defineEmits(["back", "save", "discard"])
</script>
