<template>
  <div class="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
    <div class="i-carbon-document-unknown text-4xl text-warm-400" />
    <div class="text-sm text-warm-700 dark:text-warm-300">
      {{ t("studio.module.notFound.title", { kind, name }) }}
    </div>
    <div class="text-xs text-warm-500 max-w-sm">
      {{ t("studio.module.notFound.hint") }}
    </div>
    <KButton variant="secondary" size="sm" icon="i-carbon-arrow-left" @click="$emit('back')">
      {{ t("studio.frame.back") }}
    </KButton>
  </div>
</template>

<script setup>
import KButton from "@/components/studio/common/KButton.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
})

defineEmits(["back"])
</script>
